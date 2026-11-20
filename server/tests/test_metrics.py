"""SRV-FEAT-501 — `E32 GET /api/metrics`: the playbook's stage numbers, computed on request.

`SPEC.md` §4.21 and §5 `E32`; `FAC-EPIC-375` §1.4 and §3. Five things a person reads about the
process itself — the share of tickets whose FIRST QA verdict was a pass, rounds per ticket, the
minutes a round spends per stage, the hours from a ticket's first stage to its landing, the
escapes filed and the tickets whose §1 was edited after work started — over a window of N days,
reduced from the run records and documents the tool already holds.

**The arbiter is a recording, not this file.** `docs/reports/FAC-EPIC-374/measure-rounds.py`
computed these numbers first, once per epic, by hand. It is the only written definition of
*active minutes* and *stage minutes* this repository has, so case 1 runs it as a subprocess over
the same fixture the server is pointed at, parses its stdout, and requires the endpoint to agree
field by field. A C++ reduction checked only against assertions written beside it would agree
with itself and with nothing else — `_TEMPLATE.md` §2's second way a check lies.

Every fixture here has members that DISAGREE, because every number under test is an aggregate and
`min`, `max`, `first`, `any` and `all` are indistinguishable on a singleton (`SRV-FEAT-165`):

* the stage durations are chosen so median and mean differ for every stage;
* the QA fixture holds a pass-first-time, a fail-then-pass and a QA round that left no verdict
  document, so the rate is a third and not a half or a whole;
* the landing fixture's landing order is not its starting order, so the median differs from both
  ends;
* the window fixture holds one member of every series inside the window and one outside it.

    cd server && pytest tests/test_metrics.py -v
"""

import json
import os
import re
import statistics
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jsonschema
import pytest
import requests

FACTORY = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((FACTORY / "docs/technical/contracts/http.schema.json").read_text())
ARBITER = FACTORY / "docs" / "reports" / "FAC-EPIC-374" / "measure-rounds.py"

E32_BODY = dict(SCHEMA["responses"]["E32"],
                **{"$schema": SCHEMA["$schema"], "$defs": SCHEMA["$defs"]})
E32_VALIDATOR = jsonschema.Draft202012Validator(E32_BODY)

ISO = "%Y-%m-%dT%H:%M:%SZ"


def arbiter_stage_vocabulary():
    """The stage names `measure-rounds.py` will PRINT, read out of the script itself.

    The script's stage table iterates a `STAGES` list of its own, so a fixture reporting a stage
    outside it produces a row the endpoint carries and the arbiter does not — a disagreement
    about vocabulary read as a disagreement about arithmetic. Derived, never typed: a stage added
    to the script tomorrow is available here tomorrow.
    """
    found = re.search(r"^STAGES\s*=\s*\[(.*?)\]", ARBITER.read_text(), re.M | re.S)
    assert found, f"{ARBITER} no longer declares a STAGES list this file can read"
    return re.findall(r"'([A-Z]+)'", found.group(1))


# ── the fixture repository ───────────────────────────────────────────────────────────────────

def git(root, *args, when=None):
    env = {}
    if when is not None:
        stamp = when.strftime(ISO)
        env = {"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
    full = dict(os.environ, **env)
    out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                         env=full, timeout=60)
    assert out.returncode == 0, f"git {' '.join(args)} failed: {out.stderr}"
    return out.stdout


QA_ROLE = {"qa": {"start_on_stage": "QA", "lane": "new",
                  "prompt_file": ".factory/prompts/qa.md"}}


def build_repo(root, *, stages=("SPEC", "IMPL", "GATE"), roles=QA_ROLE, escapes=True,
               make_git=True):
    """A repository the server can be pointed at, with a git history and a ledger.

    `docs.wiki` is deliberately the whole `docs` directory, exactly as `.factory/factory.json`
    declares it here: the landing derivation excludes every path the manifest called a document,
    and this is the declaration that makes that set equal `test_main_history.py`'s `docs/`.
    """
    root = Path(root)
    (root / ".factory").mkdir(parents=True, exist_ok=True)
    manifest = {
        "factory": "0.1",
        "workspaces": {"core": {"path": "."}},
        "docs": {"epics": "docs/epics", "tickets": "docs/tickets", "reports": "docs/reports",
                 "wiki": "docs"},
        "stages": list(stages),
    }
    if escapes:
        manifest["docs"]["escapes"] = "docs/operations/ESCAPES.md"
    if roles:
        manifest["roles"] = roles
        for name, decl in roles.items():
            p = root / decl["prompt_file"]
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"You are the {name}. Check {{ticket_id}}.\n")
    (root / ".factory" / "factory.json").write_text(json.dumps(manifest, indent=2))
    (root / ".gitignore").write_text(
        ".factory/worktrees/\n.factory/runs/\n.factory/ids.json\n.factory/lanes/\n")
    for d in ("docs/epics", "docs/tickets", "docs/reports", "src"):
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text("a repository the factory is pointed at\n")
    if not make_git:
        return root
    git(root, "init", "-b", "main")
    git(root, "config", "user.email", "factory@test.local")
    git(root, "config", "user.name", "factory tests")
    git(root, "config", "commit.gpgsign", "false")
    git(root, "add", "-A")
    git(root, "commit", "-m", "initial")
    return root


def ticket(root, tid, *, title=None, status="todo", stages=None):
    text = f"---\nid: {tid}\ntitle: {title or ('ticket ' + tid)}\nworkspace: core\n"
    text += f"status: {status}\n"
    if stages:
        text += "stages: " + " ".join(stages) + "\n"
    text += "---\n\nbody\n"
    p = root / "docs" / "tickets" / f"{tid}.md"
    p.write_text(text)
    return p


def verdict(root, tid, text="PASS\n"):
    p = root / "docs" / "reports" / f"{tid}-qa.md"
    p.write_text(text)
    return p


def ledger(root, dates):
    """A ledger in this repository's own shape: a first markdown table with a Date column."""
    rows = "\n".join(f"| {d} | something got past a gate | TKT-000 | a person | a check | a fix |"
                     for d in dates)
    (root / "docs/operations/ESCAPES.md").write_text(
        "# Escapes\n\n## Ledger\n\n"
        "| Date | Escape | Escaped from | Found by | Root cause of the MISS | Change landed |\n"
        "|------|--------|--------------|----------|------------------------|---------------|\n"
        + rows + "\n")


def land(root, tid, when, *, subject=None):
    """One landing: a commit on `main` naming the ticket and touching a path outside `docs/`."""
    (root / "src" / f"{tid.lower()}.c").write_text(f"/* {tid} */\nint {tid.replace('-', '_')};\n")
    git(root, "add", "-A")
    git(root, "commit", "-m", subject or f"{tid}: what a person can now do", when=when)


def touch_document(root, tid, when, *, note="a §1 edit after the round started"):
    p = root / "docs" / "tickets" / f"{tid}.md"
    p.write_text(p.read_text() + f"\n{note}\n")
    git(root, "add", "-A")
    git(root, "commit", "-m", f"{tid}: {note}", when=when)


def plant_run(root, run_id, tid, started, *, stages=(), turns=(), role="run", duration=None,
              cost=1.0, tail=True):
    """One run record as `agent::history` writes one, with the transcript the arbiter reads.

    `stages` is [(name, minutes)] — the gap between one `stage` frame and the next is the stage's
    time, and the last one runs to the run's last WORKING entry, which is why `tail` plants a
    non-turn entry at the end. `turns` are `turn` durations in seconds; their sum is the run's
    active time. Both definitions are `measure-rounds.py`'s, verbatim (`server/design/metrics.md`).

    **`role` defaults to the literal `"run"`, because that is what the TOOL writes.** Measured
    against a real repository on 2026-09-11, driving E12/E18 by hand:

        $ cat .factory/runs/run-2-7cf18c86/meta.json
        {"run_id":"run-2-7cf18c86","lane":"lane/02","ticket_id":"SRV-FEAT-498", … ,"role":"run", … }

    A run lane's record carries `role: "run"` — the manifest declares no such role, and `E31`
    resolves an undeclared name to the fallback `run`. Planting the key ABSENT is a convenient
    input the product never produces, and a fixture built that way agrees with any handler that
    tests the key for emptiness. It did: `rounds_per_ticket`, `stage_minutes` and
    `active_minutes` were all `null` over three real runs while every case here was green.
    """
    d = Path(root) / ".factory" / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)

    cursor = started
    lines, seq = [], 0

    def emit(entry, at):
        nonlocal seq
        seq += 1
        lines.append(json.dumps(dict({"seq": seq, "ts": at.strftime(ISO)}, **entry)))

    for i, (name, minutes) in enumerate(stages):
        emit({"kind": "stage", "stage": name, "round": 1}, cursor)
        if i < len(turns):
            emit({"kind": "turn", "n": i + 1, "duration_s": turns[i], "cost_usd": 0.1},
                 cursor + timedelta(seconds=1))
        cursor = cursor + timedelta(minutes=minutes)
    for extra in turns[len(stages):]:
        emit({"kind": "turn", "n": 99, "duration_s": extra, "cost_usd": 0.1}, cursor)
    if tail:
        emit({"kind": "text", "text": "done"}, cursor)

    span = int((cursor - started).total_seconds())
    meta = {"run_id": run_id, "lane": "lane/01", "ticket_id": tid,
            "session_id": f"sess-{run_id}", "n": 1,
            "started_iso": started.strftime(ISO),
            "ended_iso": cursor.strftime(ISO), "outcome": "completed",
            "duration_s": duration if duration is not None else max(span, 1),
            "cost_usd": cost, "turns": len(turns), "exit_code": 0, "signal": None,
            "unmapped": {}}
    if role is not None:
        meta["role"] = role
    (d / "meta.json").write_text(json.dumps(meta))
    (d / "transcript.jsonl").write_text("\n".join(lines) + "\n")
    return d


def metrics(srv, query=""):
    r = srv.get("/api/metrics" + query)
    assert r.status_code == 200, f"/api/metrics{query} → {r.status_code} {r.text}"
    body = r.json()
    E32_VALIDATOR.validate(body)
    return body


# ── the arbiter, run and parsed ──────────────────────────────────────────────────────────────

HEAD = re.compile(r"^== (\w+): n=(\d+)\s+wall=([\d.]+)h\s+active=([\d.]+)h\s+"
                  r"median active=(\d+)m\s+median cost=\$(\d+)")
ROW = re.compile(r"^\s{3}([A-Z]+)\s+(\d+)\s+([\d.]+)\s+([\d.]+)\s+(\d+)\s*$")


def run_arbiter(root, min_s=1):
    """`measure-rounds.py` over the fixture's own run records, as a subprocess.

    `--min-s 1` makes the script's short-run filter provably inert: the endpoint has no such knob
    (`server/design/metrics.md`), and a comparison taken where the arbiter drops runs the endpoint
    keeps compares two populations rather than two definitions.
    """
    out = subprocess.run(["python3", str(ARBITER), str(Path(root) / ".factory" / "runs"),
                          "--min-s", str(min_s)],
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, f"the arbiter refused the fixture:\n{out.stdout}\n{out.stderr}"
    roles, stage_rows = {}, {}
    for line in out.stdout.splitlines():
        head = HEAD.match(line)
        if head:
            roles[head.group(1)] = {"n": int(head.group(2)), "wall_h": head.group(3),
                                    "active_h": head.group(4),
                                    "median_active_m": head.group(5),
                                    "median_cost": head.group(6)}
            continue
        row = ROW.match(line)
        if row and row.group(1) != "stage":
            stage_rows[row.group(1)] = {"n": int(row.group(2)), "median": row.group(3),
                                        "mean": row.group(4), "max": row.group(5)}
    assert roles, f"the arbiter printed no role line — nothing was measured:\n{out.stdout}"
    return {"roles": roles, "stages": stage_rows, "stdout": out.stdout}


def close(a, b, tol=0.05):
    return a is not None and abs(float(a) - float(b)) <= tol


def printed_as(value, recorded, digits=1):
    """The endpoint's number, put through the arbiter's OWN formatting and compared as text.

    The script prints one decimal place. Comparing its `26.2` to a served `26.25` with a
    tolerance makes the check depend on which side of `0.05` the subtraction lands, which is a
    property of binary floating point and not of either number. Formatting both the same way
    compares the two values at the precision the recording actually has.
    """
    return value is not None and f"{float(value):.{digits}f}" == str(recorded)


# ── 1 · the endpoint and the recorded arbiter agree, field by field ───────────────────────────

def test_the_endpoint_agrees_with_the_recorded_script(tmp_path, serve):
    """§1.1 — every number `E32` serves equals `measure-rounds.py`'s over the SAME fixture.

    Four rounds over three tickets, plus ONE QA run whose stage frames are minutes long and
    whose stages are the same names — so an endpoint that reduced every run rather than the
    rounds nobody declared a role for would move every median and disagree with the arbiter,
    which counts the two roles separately and builds its stage table from `run` alone.

    The stage durations are chosen so median and mean differ for BOTH stages: a fixture on which
    they coincide cannot tell a median from a mean.
    """
    root = build_repo(tmp_path / "repo")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    for tid in ("TKT-101", "TKT-102", "TKT-103"):
        ticket(root, tid)

    vocabulary = arbiter_stage_vocabulary()
    assert {"SPEC", "IMPL"} <= set(vocabulary), (
        f"the arbiter no longer prints the stages this fixture reports: {vocabulary}")

    # (ticket, run id, SPEC minutes, IMPL minutes, active seconds)
    rounds = [("TKT-101", "run-a", 10, 20, 600),
              ("TKT-101", "run-b", 20, 45, 1200),
              ("TKT-102", "run-c", 30, 10, 1800),
              ("TKT-103", "run-d", 60, 30, 3600)]
    for i, (tid, rid, spec_m, impl_m, active) in enumerate(rounds):
        plant_run(root, rid, tid, now - timedelta(days=5, hours=i),
                  stages=[("SPEC", spec_m), ("IMPL", impl_m)], turns=[active])
    plant_run(root, "run-qa", "TKT-101", now - timedelta(days=4),
              stages=[("SPEC", 300), ("IMPL", 300)], turns=[9000], role="qa")

    srv = serve(root)
    body = metrics(srv, "?days=30")
    arb = run_arbiter(root)

    assert arb["roles"]["run"]["n"] == 4, arb["stdout"]
    assert arb["roles"]["qa"]["n"] == 1, arb["stdout"]
    assert set(arb["stages"]) == {"SPEC", "IMPL"}, arb["stdout"]

    # the window echoes what was asked, and `runs` is every record in it — both roles
    assert body["window_days"] == 30, body
    assert body["runs"] == arb["roles"]["run"]["n"] + arb["roles"]["qa"]["n"], (
        f"E32 counted {body['runs']} runs; the arbiter measured "
        f"{arb['roles']['run']['n']} + {arb['roles']['qa']['n']}\n{arb['stdout']}")

    # stage medians, means and populations — field by field against the recording
    served = {row["name"]: row for row in body["stage_minutes"]}
    assert set(served) == set(arb["stages"]), (
        f"E32's stages {sorted(served)} against the arbiter's {sorted(arb['stages'])}\n"
        f"{arb['stdout']}")
    for name, recorded in arb["stages"].items():
        assert served[name]["n"] == recorded["n"], (name, served[name], recorded)
        assert printed_as(served[name]["median"], recorded["median"]), (
            f"{name} median: E32 says {served[name]['median']}, the arbiter recorded "
            f"{recorded['median']}\n{arb['stdout']}")
        assert printed_as(served[name]["mean"], recorded["mean"]), (
            f"{name} mean: E32 says {served[name]['mean']}, the arbiter recorded "
            f"{recorded['mean']}\n{arb['stdout']}")
    # and the two really are different numbers, so the comparison could have failed
    assert served["SPEC"]["median"] != served["SPEC"]["mean"], served["SPEC"]
    assert served["IMPL"]["median"] != served["IMPL"]["mean"], served["IMPL"]

    # active minutes — the arbiter's own definition, sum of `turn` durations
    assert printed_as(body["active_minutes"]["median"],
                      arb["roles"]["run"]["median_active_m"], 0), (
        f"E32 active median {body['active_minutes']['median']}m against the arbiter's "
        f"{arb['roles']['run']['median_active_m']}m\n{arb['stdout']}")
    assert body["active_minutes"]["n"] == arb["roles"]["run"]["n"], body["active_minutes"]
    assert printed_as(body["active_minutes"]["mean"] * body["active_minutes"]["n"] / 60,
                      arb["roles"]["run"]["active_h"], 1), (
        f"E32's total active hours against the arbiter's {arb['roles']['run']['active_h']}h\n"
        f"{arb['stdout']}")

    # rounds — the arbiter's `n` is every round it measured, over the three tickets that have one
    tickets_with_rounds = len({tid for tid, *_ in rounds})
    assert close(body["rounds_per_ticket"]["mean"],
                 arb["roles"]["run"]["n"] / tickets_with_rounds), (
        f"E32 mean rounds {body['rounds_per_ticket']['mean']} against the arbiter's "
        f"{arb['roles']['run']['n']} rounds over {tickets_with_rounds} tickets")
    assert close(body["rounds_per_ticket"]["median"],
                 statistics.median([2, 1, 1])), body["rounds_per_ticket"]


# ── 2 · first pass is a PAIR, and a QA round that wrote nothing is not one ────────────────────

def test_first_pass_needs_the_verdict_pair(tmp_path, serve):
    """§1.2 — `first_pass_qa_rate` is 1/3 over a pass-first-time, a fail-then-pass and a QA
    round that left no verdict document; removing the first's verdict document makes it 0/3.

    §3.5b: the verdict of record is a PAIR — a verdict document beside the report AND the
    ticket's own `status:`. A session marking its own work passed is exactly what the second
    session exists to prevent, so the claim alone is not the verdict.

    A ticket whose QA round wrote nothing is in the DENOMINATOR and not in the numerator:
    *nobody wrote the verdict down* is an outcome of a round, not the absence of one, and
    dropping it would make the rate improve every time a session died before it could write.
    """
    root = build_repo(tmp_path / "repo")
    now = datetime.now(timezone.utc).replace(microsecond=0)

    ticket(root, "TKT-201", status="pass")
    ticket(root, "TKT-202", status="pass")
    ticket(root, "TKT-203", status="pass")
    verdict(root, "TKT-201")
    verdict(root, "TKT-202")
    # TKT-203 has a QA round and no verdict document, deliberately.

    for tid in ("TKT-201", "TKT-202", "TKT-203"):
        plant_run(root, f"round-{tid}", tid, now - timedelta(days=3),
                  stages=[("SPEC", 10), ("IMPL", 20)], turns=[600])
    plant_run(root, "qa-201", "TKT-201", now - timedelta(days=2),
              stages=[("QA", 10)], turns=[300], role="qa")
    plant_run(root, "qa-202a", "TKT-202", now - timedelta(days=2),
              stages=[("QA", 10)], turns=[300], role="qa")
    plant_run(root, "qa-202b", "TKT-202", now - timedelta(days=1),
              stages=[("QA", 10)], turns=[300], role="qa")
    plant_run(root, "qa-203", "TKT-203", now - timedelta(days=2),
              stages=[("QA", 10)], turns=[300], role="qa")

    srv = serve(root)
    body = metrics(srv, "?days=30")

    assert body["tickets_with_verdict"] == 3, (
        f"the denominator is the tickets a verdict was RUN for: {body}")
    assert close(body["first_pass_qa_rate"], 1 / 3, 0.001), (
        f"one of three passed first time; E32 says {body['first_pass_qa_rate']}")

    # the pair, from the other side: take the document away and the same ticket stops counting
    (root / "docs" / "reports" / "TKT-201-qa.md").unlink()
    after = metrics(srv, "?days=30")
    assert after["tickets_with_verdict"] == 3, after
    assert after["first_pass_qa_rate"] == 0, (
        "with no verdict document beside it, `status: pass` is a claim and not a verdict — "
        f"E32 still says {after['first_pass_qa_rate']}")
    assert after["first_pass_qa_rate"] is not None, (
        "0 of 3 is a measurement; `null` would say nothing reached QA, which is false here")


# ── 3 · the first DECLARED stage, and the landing on the base branch ──────────────────────────

def test_spec_to_land_reads_the_first_declared_stage_and_the_landing(tmp_path, serve):
    """§1.3 — from the first frame naming the ticket's OWN first stage to its landing commit.

    The two sources are forced apart in both directions, because a fixture where they agree
    passes identically whichever one the code read (`SRV-FEAT-065`):

    * `TKT-301` declares `stages: DESIGN SPEC TEST` and its run reports **SPEC before DESIGN**.
      An endpoint keyed on the literal `SPEC` starts its clock four hours early and every hour
      below moves.
    * the landing order is not the starting order — `TKT-302` starts last and lands first — so
      the median is not the first, the last, the min or the max.
    * `TKT-303` has a round and no landing at all, and is not in `n`.
    """
    root = build_repo(tmp_path / "repo")
    now = datetime.now(timezone.utc).replace(microsecond=0)

    ticket(root, "TKT-301", stages=["DESIGN", "SPEC", "TEST"])
    ticket(root, "TKT-302")
    ticket(root, "TKT-303")
    ticket(root, "TKT-304")
    git(root, "add", "-A")
    git(root, "commit", "-m", "the tickets", when=now - timedelta(days=12))

    # TKT-301 reports SPEC four hours BEFORE the DESIGN frame its own sequence starts with
    plant_run(root, "run-301", "TKT-301", now - timedelta(days=5, hours=4),
              stages=[("SPEC", 240), ("DESIGN", 60), ("TEST", 60)], turns=[600])
    plant_run(root, "run-302", "TKT-302", now - timedelta(days=9),
              stages=[("SPEC", 60), ("IMPL", 60)], turns=[600])
    plant_run(root, "run-303", "TKT-303", now - timedelta(days=8),
              stages=[("SPEC", 60), ("IMPL", 60)], turns=[600])
    plant_run(root, "run-304", "TKT-304", now - timedelta(days=10),
              stages=[("SPEC", 60), ("IMPL", 60)], turns=[600])

    # started 304 → 302 → 303 → 301; landed 302 → 304 → 301, and 303 never
    land(root, "TKT-302", now - timedelta(days=7))     # started -9d,  landed -7d →  48 h
    land(root, "TKT-304", now - timedelta(days=3))     # started -10d, landed -3d → 168 h
    land(root, "TKT-301", now - timedelta(days=2))     # DESIGN at -5d, landed -2d →  72 h

    srv = serve(root)
    body = metrics(srv, "?days=30")

    assert body["spec_to_land_hours"]["n"] == 3, (
        f"a ticket with no landing is not in n: {body['spec_to_land_hours']}")
    assert close(body["spec_to_land_hours"]["median"], 72, 0.05), (
        f"the median of 48h, 72h and 168h is 72h; E32 says "
        f"{body['spec_to_land_hours']['median']}")
    # …a middle value, not an end of the range — the landing order is not the starting order
    assert 48 < body["spec_to_land_hours"]["median"] < 168, body["spec_to_land_hours"]

    # the DESIGN/SPEC discrimination, stated as the number it moves: TKT-301 IS the median, so
    # a clock started at the literal `SPEC` frame makes the served median 76 h instead of 72 h.
    assert not close(body["spec_to_land_hours"]["median"], 76, 0.05), (
        "the clock started at the literal `SPEC` frame rather than at DESIGN, which is the "
        f"first stage TKT-301's own `stages:` line names: {body['spec_to_land_hours']}")


# ── 4 · the window bounds every series, each by its own clock ─────────────────────────────────

def test_the_window_bounds_every_series(tmp_path, serve):
    """§1.4 — `days` bounds the runs by `started_iso`, the ledger rows by the row's date and the
    landings by the commit date; and `rework_after_build` counts a ticket whose own document was
    committed after its first round started.

    One member of every series is inside a 30-day window and one is outside it, so shrinking the
    window to 7 days changes EVERY count. A window that moved one number and not the others
    would pass a check written per number.
    """
    root = build_repo(tmp_path / "repo")
    now = datetime.now(timezone.utc).replace(microsecond=0)

    ticket(root, "TKT-401")           # inside the window, and its §1 is edited mid-round
    ticket(root, "TKT-402")           # outside the 7-day window, inside the 30-day one
    git(root, "add", "-A")
    git(root, "commit", "-m", "the tickets", when=now - timedelta(days=25))

    plant_run(root, "run-401", "TKT-401", now - timedelta(days=2),
              stages=[("SPEC", 10), ("IMPL", 20)], turns=[600])
    plant_run(root, "run-402", "TKT-402", now - timedelta(days=20),
              stages=[("SPEC", 10), ("IMPL", 20)], turns=[600])

    touch_document(root, "TKT-401", now - timedelta(days=1))
    touch_document(root, "TKT-402", now - timedelta(days=19))
    land(root, "TKT-401", now - timedelta(days=1, hours=1))
    land(root, "TKT-402", now - timedelta(days=19, hours=1))

    ledger(root, [(now - timedelta(days=3)).strftime("%Y-%m-%d"),
                  (now - timedelta(days=21)).strftime("%Y-%m-%d")])

    srv = serve(root)
    wide = metrics(srv, "?days=30")
    narrow = metrics(srv, "?days=7")

    assert wide["runs"] == 2 and narrow["runs"] == 1, (wide["runs"], narrow["runs"])
    assert wide["escapes_in_window"] == 2 and narrow["escapes_in_window"] == 1, (
        f"the ledger's date cell did not bound the count: {wide['escapes_in_window']} / "
        f"{narrow['escapes_in_window']}")
    assert wide["spec_to_land_hours"]["n"] == 2 and narrow["spec_to_land_hours"]["n"] == 1, (
        wide["spec_to_land_hours"], narrow["spec_to_land_hours"])
    assert wide["rework_after_build"]["tickets"] == 2, wide["rework_after_build"]
    assert narrow["rework_after_build"]["tickets"] == 1, narrow["rework_after_build"]
    assert close(wide["rework_after_build"]["share"], 1.0), wide["rework_after_build"]
    assert narrow["window_days"] == 7 and wide["window_days"] == 30, (wide, narrow)

    # rework is about a commit AFTER the round started, and not about any commit: a ticket whose
    # document was only ever written before its first round does not count.
    ticket(root, "TKT-403")
    git(root, "add", "-A")
    git(root, "commit", "-m", "TKT-403: filed", when=now - timedelta(days=4))
    plant_run(root, "run-403", "TKT-403", now - timedelta(days=3),
              stages=[("SPEC", 10), ("IMPL", 20)], turns=[600])
    later = metrics(srv, "?days=7")
    assert later["rework_after_build"]["tickets"] == 1, (
        f"a ticket filed BEFORE its round started was counted as rework: "
        f"{later['rework_after_build']}")
    assert close(later["rework_after_build"]["share"], 0.5), later["rework_after_build"]


# ── 5 · NEGATIVE · a refused window, an empty repository, and an engine that stays free ───────

def test_bad_windows_are_refused_and_empty_is_null_and_the_engine_stays_free(tmp_path, serve):
    """§1.5 MANDATORY NEGATIVE — three refusals, one empty answer and one liveness measurement.

    `?days=abc`, `?days=0` and `?days=-3` are `400 bad_window`, and the refusal names the
    parameter and echoes what was sent. A repository with no run records answers `200` with
    `runs: 0` and every RATE `null` — never `0`, because *measured and none qualified* and
    *nothing was measured* are different facts about a repository and a screen cannot recover
    one from the other (`server/design/metrics.md`).

    And the walk is on a worker: `E1` answers inside 200 ms while `E32` is reducing 500 runs.
    Blocking one of four io threads stalls everything queued behind it (`SRV-FIX-108`), and the
    only form of that rule a test can hold is a second request measured while the first runs.
    """
    root = build_repo(tmp_path / "repo")
    srv = serve(root)

    for bad in ("abc", "0", "-3", "3651", "2.5", ""):
        r = srv.get(f"/api/metrics?days={bad}")
        assert r.status_code == 400, f"?days={bad!r} → {r.status_code} {r.text}"
        body = r.json()
        assert body["error"]["code"] == "bad_window", body
        assert body["error"]["detail"]["param"] == "days", body
        assert body["error"]["detail"]["days"] == bad, body

    # …and a window this endpoint CAN honour is not refused, so the refusal is about the value
    assert srv.get("/api/metrics?days=3650").status_code == 200
    assert srv.get("/api/metrics?days=1").status_code == 200

    empty = metrics(srv)
    assert empty["window_days"] == 30, empty
    assert empty["runs"] == 0, empty
    assert empty["tickets_with_verdict"] == 0, empty
    assert empty["escapes_in_window"] == 0, empty
    assert empty["rework_after_build"]["tickets"] == 0, empty
    assert empty["spec_to_land_hours"]["n"] == 0, empty
    assert empty["stage_minutes"] == [], empty
    for where, value in (("first_pass_qa_rate", empty["first_pass_qa_rate"]),
                         ("rounds_per_ticket.median", empty["rounds_per_ticket"]["median"]),
                         ("rounds_per_ticket.mean", empty["rounds_per_ticket"]["mean"]),
                         ("active_minutes.median", empty["active_minutes"]["median"]),
                         ("active_minutes.mean", empty["active_minutes"]["mean"]),
                         ("spec_to_land_hours.median", empty["spec_to_land_hours"]["median"]),
                         ("rework_after_build.share", empty["rework_after_build"]["share"])):
        assert value is None, (
            f"{where} is {value!r} over a repository where nothing was measured — `0` says a "
            f"process failed and `null` says nothing ran, and they are different facts")

    # the engine, while E32 walks 500 runs
    big = build_repo(tmp_path / "big")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    for i in range(500):
        tid = f"TKT-{500 + i % 40}"
        ticket(big, tid)
        plant_run(big, f"bulk-{i:04d}", tid, now - timedelta(days=1, minutes=i),
                  stages=[("SPEC", 10), ("IMPL", 20)], turns=[600])
    heavy = serve(big)

    answer = {}

    def walk():
        answer["r"] = requests.get(heavy.base + "/api/metrics?days=30", timeout=120)

    t = threading.Thread(target=walk, daemon=True)
    t.start()

    samples, worst = [], 0.0
    deadline = time.time() + 120
    while t.is_alive() and time.time() < deadline:
        started = time.time()
        health = requests.get(heavy.base + "/api/health", timeout=10)
        elapsed = time.time() - started
        assert health.status_code == 200, health.text
        samples.append(elapsed)
        worst = max(worst, elapsed)
    t.join(timeout=120)

    assert samples, "E32 answered before a single health check could be taken beside it"
    assert worst < 0.2, (
        f"the worst of {len(samples)} /api/health calls taken while E32 reduced 500 runs was "
        f"{worst:.3f}s — the walk is on an io thread")
    assert answer["r"].status_code == 200, answer["r"].text
    assert answer["r"].json()["runs"] == 500, answer["r"].json()["runs"]
