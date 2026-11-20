"""SRV-FEAT-022 — a declared role starts itself when its stage is reached (F31, E22, M11).

The process says a ticket is checked by a second session that starts cold, and three tickets
reached `main` without one — not because anyone disagreed with the rule, but because the last line
of each report was a polite request that a person start it. A step that needs someone to notice is
a step that gets skipped.

So the repository declares roles and **the tool starts them**. Everything this file watches for is
on the boundary that makes that safe rather than clever:

- **the tool attaches no meaning to a role name.** `reviewer` is a string, `start_on_stage` is a
  string it compares, and every word of meaning lives in the prompt file the repository commits.
  There is no built-in role and no default (SPEC.md §10, F31).
- **a new lane, not a new run.** A new worktree, a new branch and a new session, so the started
  role cannot inherit the transcript, the reasoning or the framing of the lane that triggered it.
  That is the criterion the whole feature exists for, and it is asserted rather than asked for.
- **three guards, and the negative is about the third.** Once per ticket per stage per role; a lane
  seat, which DEFERS rather than drops; and a role that cannot start itself. Without the last one a
  misconfigured manifest fills every seat the machine has.

Every case builds its own synthetic repository. Never this one: a roles module that only works
against the repository it was written in has learned that repository.
"""

# ── SRV-FIX-184 · THE TWO SOURCES A ROLE FIXTURE MUST FORCE APART ────────────────────────────
#
# A role lane has two candidate bases, and for most of this file's life they were the same commit:
# the MAIN CHECKOUT's `HEAD`, and `ticket/<id>` — the branch the run lane's work is on. Every case
# below that builds a role lane and says nothing about its base was measured where the two agreed,
# and a fixture in which they agree proves only that the code read one of them.
#
# So: any fixture that asserts anything about what a role lane CONTAINS commits in the run lane's
# worktree AND moves the main checkout's `HEAD`, so that the two sources disagree before the role
# is claimed. `SRV-FEAT-065` asserted "the branch it is cut from" green against a fixture where
# they coincided, and the tool shipped a QA lane carrying three commits of a concurrent session
# and none of the fix it existed to judge (`docs/operations/ESCAPES.md`, 2026-08-21).
#
# ── SRV-FIX-189 · AND THE SECOND THING EVERY ROLE FIXTURE MUST FORCE: THE RE-CLAIM ───────────
#
# The rule above, one round later. `E13` always keeps the branch, so a role claimed a second time
# takes a DIFFERENT path through `lane::git::addWorktree` — the branch already exists, and that
# path discarded the base entirely until this ticket. Every one of `SRV-FIX-184`'s six criteria
# measured the first claim, so the discard was invisible to all of them, and the round after a QA
# FAIL — the one round where the run branch has certainly moved — read the round before it.
#
# So a fixture that asserts what a role lane CONTAINS claims the role TWICE, releasing it and
# committing on the run branch in between. Acquiring a resource once proves the create path works
# and says nothing about the path every real round takes.

import json
import os
import time
from pathlib import Path

import jsonschema
import pytest

import stand_in
from test_lane import branch_exists, build_repo, git

DOCS = Path(__file__).resolve().parents[2] / "docs"
SOCKET_SCHEMA = json.loads((DOCS / "technical/contracts/socket.schema.json").read_text())
SOCKET_VALIDATOR = jsonschema.Draft202012Validator(SOCKET_SCHEMA)


def valid(frame):
    SOCKET_VALIDATOR.validate(frame)
    return frame


# The stand-in records the two things this ticket is about: what it was STARTED WITH, and where it
# is standing. It reads its stdin exactly the way the captured CLI does (tests/stand_in.py), so a
# prompt this suite says was delivered is a prompt the real dependency would have read.
AGENT = r"""#!/bin/sh
log=./agent-log.txt
: > "$log"
""" + stand_in.FRAME_READER + stand_in.PATH_PROBE + r"""
echo "run=${FACTORY_RUN_ID}" >> "$log"
echo "token=${FACTORY_PROGRESS_TOKEN}" >> "$log"
echo "cwd=`pwd`" >> "$log"
echo "ready" >> "$log"

while : ; do
  if read_frame ; then
    printf '%s\n' "msg<<<${text}>>>" >> "$log"
    probe_paths
    case "$text" in
      bye) break ;;
      emit:*)
        f=`printf '%s' "$text" | cut -d: -f2`
        cat "./$f"
        echo "emitted=$f" >> "$log"
        ;;
    esac
  else
    break
  fi
done
echo "eof" >> "$log"
"""

# The repository's own words, and the tool reads no further than the four placeholders it defines.
# `{not_a_placeholder}` is here on purpose: a tool that blanks what it does not recognise is a tool
# that silently edits somebody's prompt.
PROMPT = """You are checking {ticket_id}.

The ticket is at {ticket_path}, in the repository at {repo_root}.
It was handed over by {triggered_by_run}.
Leave {not_a_placeholder} exactly as it is.
"""

ROLE = {"reviewer": {"start_on_stage": "GATE",
                     "lane": "new",
                     "prompt_file": ".factory/prompts/reviewer.md"}}


def real(path):
    return os.path.realpath(str(path))


@pytest.fixture
def env(tmp_path, serve):
    """A repository declaring roles, a server, and just enough to drive one lane and one run."""
    conns = []
    made = []

    class Env:
        def build(self, *, roles=ROLE, lanes_host=3, prompt=PROMPT, write_prompt=True,
                  tickets=("TCK-001", "TCK-002"), resources=None, workspaces=None):
            root = build_repo(tmp_path / f"repo{len(made)}",
                              lanes_host=lanes_host,
                              resources=resources,
                              workspaces=workspaces,
                              agent={"command": "/bin/sh agent.sh"},
                              tickets=tickets)
            (root / "agent.sh").write_text(AGENT)
            if roles is not None:
                manifest = json.loads((root / ".factory" / "factory.json").read_text())
                manifest["roles"] = roles
                (root / ".factory" / "factory.json").write_text(json.dumps(manifest, indent=2))
            if write_prompt:
                p = root / ".factory" / "prompts" / "reviewer.md"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(prompt)
            git(root, "add", "-A")
            git(root, "commit", "-m", "the reporting agent, and a prompt the repository owns")
            made.append(root)
            self.root = root
            self.srv = serve(root, FACTORY_TEST_HOOKS="1")
            return self

        # ── lanes and runs ───────────────────────────────────────────────────────────────────
        def claim(self, ticket="TCK-001", workspace="core", role=None):
            body = {"ticket_id": ticket, "workspace": workspace}
            if role is not None:
                body["role"] = role
            r = self.srv.post("/api/lanes", json=body)
            assert r.status_code == 201, r.text
            return r.json()["lane"]

        def start(self, lane, prompt=None):
            body = {} if prompt is None else {"prompt": prompt}
            r = self.srv.post(f"/api/lanes/{lane}/runs", json=body)
            assert r.status_code == 201, r.text
            return r.json()

        def release(self, lane, force=True):
            r = self.srv.delete(f"/api/lanes/{lane}" + ("?force=true" if force else ""))
            assert r.status_code == 200, r.text
            return r.json()

        def say(self, lane, text):
            r = self.srv.post(f"/api/lanes/{lane}/messages", json={"text": text})
            assert r.status_code == 200, r.text
            return r.json()

        # ── what the tool says about it ──────────────────────────────────────────────────────
        def lanes(self):
            r = self.srv.get("/api/lanes")
            assert r.status_code == 200, r.text
            return r.json()["lanes"]

        def row(self, lane):
            for row in self.lanes():
                if row["lane"] == lane:
                    return row
            raise AssertionError(f"{lane} is gone; there are {[r['lane'] for r in self.lanes()]}")

        def ticket(self, ticket_id):
            r = self.srv.get(f"/api/tickets/{ticket_id}")
            assert r.status_code == 200, r.text
            return r.json()

        def transcript(self, run_id):
            r = self.srv.get(f"/api/runs/{run_id}/transcript")
            if r.status_code == 404:
                return []
            assert r.status_code == 200, r.text
            return r.json()["entries"]

        # ── the agent's own record of what it was handed ─────────────────────────────────────
        def worktree(self, lane):
            return Path(self.row(lane)["worktree"])

        def log(self, lane):
            try:
                return (self.worktree(lane) / "agent-log.txt").read_text()
            except (OSError, AssertionError):
                return ""

        def wait_log(self, lane, needle, timeout=25):
            deadline = time.time() + timeout
            while time.time() < deadline:
                if needle in self.log(lane):
                    return self.log(lane)
                time.sleep(0.05)
            raise AssertionError(
                f"{needle!r} never reached {lane}'s log; it holds:\n{self.log(lane)}")

        def token(self, lane):
            """The token that lane's run was handed — read from what the AGENT saw."""
            for line in self.wait_log(lane, "ready").splitlines():
                if line.startswith("token="):
                    return line.split("=", 1)[1]
            raise AssertionError(f"the agent never saw a token:\n{self.log(lane)}")

        def messages(self, lane):
            """Every message the agent read off its stdin, decoded, in order."""
            text = self.log(lane)
            out = []
            while "msg<<<" in text:
                _, rest = text.split("msg<<<", 1)
                if ">>>" not in rest:
                    break
                body, text = rest.split(">>>", 1)
                out.append(body)
            return out

        # ── the whole point: a lane the tool started, that nobody asked for ──────────────────
        def report(self, lane, stage, **extra):
            body = {"token": self.token(lane), "stage": stage}
            body.update(extra)
            r = self.srv.post("/api/progress", json=body)
            assert r.status_code == 202, r.text
            return r.json()

        def wait_lanes(self, n, timeout=25):
            deadline = time.time() + timeout
            while time.time() < deadline:
                rows = self.lanes()
                if len(rows) == n:
                    return rows
                time.sleep(0.05)
            raise AssertionError(
                f"expected {n} lane(s) within {timeout}s; there are "
                f"{[(r['lane'], r['role']) for r in self.lanes()]}")

        def settle(self, seconds=2.5):
            """Wait long enough that a lane which WAS going to appear would have."""
            time.sleep(seconds)
            return self.lanes()

        # ── the socket ───────────────────────────────────────────────────────────────────────
        def subscribed(self, scope):
            c = self.srv.ws()
            assert valid(c.recv())["m"] == "hello"
            c.send({"m": "subscribe", "scope": scope})
            valid(c.wait_for("snapshot"))
            conns.append(c)
            return c

        def frames(self, conn, name, seconds=3.0):
            return [valid(f) for f in conn.drain(seconds) if f.get("m") == name]

    e = Env()
    yield e
    for c in conns:
        c.close()


@pytest.fixture
def one(env):
    """A repository with one role declared, one lane, and one live run in it."""
    env.build()
    env.lane = env.claim("TCK-001")
    env.run = env.start(env.lane)
    env.wait_log(env.lane, "ready")
    return env


# ── F31 · the trigger ───────────────────────────────────────────────────────────────────────

def test_a_declared_role_starts_on_its_stage(one):
    """A lane reports `GATE` and a SECOND lane appears, on its own branch, running the prompt the
    repository wrote — and `M11` says so.

    The person is told it happened; they do not make it happen. That is the whole feature, and it
    is why the assertion is about a lane nobody asked for rather than about a return code.
    """
    c = one.subscribed({"ticket": "TCK-001"})

    one.report(one.lane, "GATE")

    rows = one.wait_lanes(2)
    started = [r for r in rows if r["lane"] != one.lane]
    assert len(started) == 1, rows
    row = started[0]

    assert row["role"] == "reviewer", row
    assert row["ticket_id"] == "TCK-001", row
    assert row["branch"] == "ticket/TCK-001-reviewer", row
    assert row["branch"] != one.row(one.lane)["branch"], "both lanes are on one branch"

    # ...running the prompt from the declared file, which the agent itself recorded reading.
    one.wait_log(row["lane"], "msg<<<")
    assert "You are checking TCK-001." in one.messages(row["lane"])[0], one.log(row["lane"])

    # ...and M11 reported the run, exactly as it reports one a person started (SPEC.md M11).
    runs = one.frames(c, "run")
    assert any(f["run_id"] == one.row(row["lane"])["run_id"] for f in runs), \
        [f.get("run_id") for f in runs]
    assert any(f["lane"] == row["lane"] for f in runs), [f.get("lane") for f in runs]


def test_role_auto_start_obeys_workspace_preflight(env):
    env.build(workspaces={"core": {"path": ".", "start_preflight": {
        "command": "case \"$(git branch --show-current)\" in *reviewer) exit 7;; esac"}}})
    lane = env.claim("TCK-001")
    run = env.start(lane)
    env.wait_log(lane, "ready")
    env.report(lane, "GATE")
    assert len(env.settle()) == 1, "a refused role preflight still left a role lane"
    runs = env.srv.get("/api/tickets/TCK-001/runs")
    assert runs.status_code == 200, runs.text
    assert len(runs.json()["runs"]) == 1, runs.text
    env.release(lane)


def test_the_started_lane_cannot_see_the_triggering_one(one):
    """THE criterion the whole feature exists for.

    A new lane is a new worktree, a new branch and a new session, so the started role cannot
    inherit the transcript, the reasoning or the framing of the lane that triggered it. A prompt
    asking it to start cold would be a request; this is the structure.
    """
    secret = "SECRET-ONLY-THE-TRIGGERING-LANE-WAS-TOLD"
    one.say(one.lane, secret)
    one.wait_log(one.lane, secret)

    one.report(one.lane, "GATE")
    rows = one.wait_lanes(2)
    row = [r for r in rows if r["lane"] != one.lane][0]
    mine = one.row(one.lane)
    one.wait_log(row["lane"], "msg<<<")
    # A lane is a real lane from the instant it is reserved, and its run starts after that — so
    # the row is read again once the started agent has spoken, not from the poll that found it.
    row = one.row(row["lane"])

    # its own worktree, and the process is standing in it
    assert row["worktree"] != mine["worktree"], row
    cwd = [l for l in one.log(row["lane"]).splitlines() if l.startswith("cwd=")]
    assert cwd, one.log(row["lane"])
    assert real(cwd[0].split("=", 1)[1]) == real(row["worktree"]), (cwd, row["worktree"])

    # a session of its own — not a continuation of the one that triggered it
    assert row["session_id"], row
    assert row["session_id"] != mine["session_id"], (row["session_id"], mine["session_id"])
    assert row["run_id"] != mine["run_id"], row

    # ...and nothing of the triggering lane reached it. Its whole input is the prompt the
    # repository wrote — not one line of the other lane's conversation, not its name, not its
    # session.
    started_log = one.log(row["lane"])
    assert secret not in started_log, started_log
    assert one.lane not in started_log, started_log
    assert mine["session_id"] not in started_log, started_log

    # The triggering RUN's id is the one thing that does cross, because the repository's own
    # prompt asked for it by name — a citation it chose, not content the tool carried over. So it
    # is counted rather than forbidden: the stand-in logs each message twice (the wire frame and
    # the decoded text), so every occurrence in its log has to be accounted for by the prompt.
    delivered = one.messages(row["lane"])[0]
    assert mine["run_id"] in delivered, "{triggered_by_run} was declared and substituted nothing"
    assert started_log.count(mine["run_id"]) == 2 * delivered.count(mine["run_id"]), (
        "the triggering run reached the started lane somewhere other than the prompt that asked "
        f"to name it:\n{started_log}")

    replay = json.dumps(one.transcript(row["run_id"]))
    for leak in (secret, one.lane, mine["session_id"]):
        assert leak not in replay, (leak, replay)


def test_the_prompt_is_the_repositorys_own_text(one):
    """The process is started with the FILE'S CONTENTS, with only the four placeholders the tool
    defines substituted — and a placeholder it does not define is left alone rather than blanked.

    Every word of meaning belongs to the repository. A tool that edited the prompt beyond the four
    values it already knows would be a tool with an opinion about what a role is (SPEC.md §10).
    """
    one.report(one.lane, "GATE")
    rows = one.wait_lanes(2)
    row = [r for r in rows if r["lane"] != one.lane][0]
    one.wait_log(row["lane"], "msg<<<")

    worktree = str(one.worktree(row["lane"]))
    expected = (PROMPT
                .replace("{ticket_id}", "TCK-001")
                .replace("{ticket_path}", lane_ticket_path(one, row["lane"]))
                .replace("{repo_root}", worktree)
                .replace("{triggered_by_run}", one.run["run_id"])).strip()

    delivered = one.messages(row["lane"])[0]
    assert delivered == expected, f"delivered:\n{delivered!r}\nexpected:\n{expected!r}"
    assert "{not_a_placeholder}" in delivered, \
        "a placeholder the tool does not define was blanked instead of left alone"


# ── SRV-FIX-055 ══════════════════════════════════════════════════════════════════════════════
#
# The same defect on this side of the seam. `roles.cpp` and `_start-run.cpp` share one substituter
# (`api::prompt`) precisely so the two cannot disagree about what `{ticket_path}` means — and they
# did not disagree: both handed the agent the MAIN checkout's path, which a lane does not bind.
# Two call sites, two cases; a fix in one is not a fix.


def lane_ticket_path(one, lane, ticket_id="TCK-001"):
    """Where the ticket is IN THAT LANE — the only filesystem its agent has."""
    rel = os.path.relpath(one.ticket(ticket_id)["path"], str(one.srv.root))
    return os.path.join(str(one.worktree(lane)), rel)


def test_a_role_started_run_gets_a_path_its_agent_can_open(one):
    """The started role OPENS the ticket it was pointed at, from inside its own lane.

    Its worktree is a different one from the lane that triggered it — a role gets its own branch
    and its own checkout — so this is also the case that notices a fix which resolves the path
    against whichever lane happens to be first.
    """
    one.report(one.lane, "GATE")
    rows = one.wait_lanes(2)
    row = [r for r in rows if r["lane"] != one.lane][0]
    one.wait_log(row["lane"], "msg<<<")

    probes = [l for l in one.log(row["lane"]).splitlines()
              if l.startswith(("opened:", "unreadable:"))]
    assert probes, (
        f"the stand-in found no absolute .md path in its prompt; the log is:\n"
        f"{one.log(row['lane'])}")
    assert all(l.startswith("opened:") for l in probes), (
        "the started role was told to read a file it cannot open:\n  " + "\n  ".join(probes)
        + f"\n\nits worktree is {one.worktree(row['lane'])}.")
    assert probes[0][len("opened:"):] == lane_ticket_path(one, row["lane"]), probes

    # NEGATIVE (mandatory): the main checkout's path is nowhere in what it was started with.
    delivered = one.messages(row["lane"])[0]
    main = one.ticket("TCK-001")["path"]
    assert main not in delivered, (
        f"the main checkout's ticket path reached the started role:\n  {main}\n\n{delivered}")


def test_a_role_starts_once_per_ticket_per_stage(one):
    """The same stage reported three times starts ONE run, not three.

    A retried stage is normal — F20 has rounds and no cap — and a fleet of duplicate checkers is
    not. The round is a fact about the repository's process; the number of processes is the tool's.
    """
    one.report(one.lane, "GATE")
    rows = one.wait_lanes(2)
    started = [r for r in rows if r["lane"] != one.lane][0]["lane"]

    one.report(one.lane, "GATE")
    one.report(one.lane, "GATE")

    after = one.settle()
    assert len(after) == 2, [(r["lane"], r["role"]) for r in after]
    assert [r["lane"] for r in after if r["role"] == "reviewer"] == [started], after

    # ...and the ticket still records the rounds, because counting them is the repository's
    # business and starting processes is the tool's.
    assert one.ticket("TCK-001")["stage"]["round"] == 3, one.ticket("TCK-001")["stage"]


def test_no_roles_block_changes_nothing(env):
    """A manifest with no `roles` behaves exactly as it did before this feature existed."""
    env.build(roles=None, write_prompt=False)
    lane = env.claim("TCK-001")
    env.start(lane)
    env.wait_log(lane, "ready")

    assert "roles" not in env.srv.get("/api/manifest").json(), \
        "the tool invented a roles block for a repository that declared none"

    env.report(lane, "GATE")
    assert [r["lane"] for r in env.settle()] == [lane], env.lanes()
    assert "roles" not in env.ticket("TCK-001"), env.ticket("TCK-001")
    assert env.ticket("TCK-001")["stage"]["index"] == 3, env.ticket("TCK-001")["stage"]


def test_a_role_naming_a_missing_prompt_file_is_refused_at_load(env):
    """`E2` reports the manifest invalid, naming the file — AT LOAD, not when the stage fires.

    A role that fails only when it is finally needed is a role that fails at the worst possible
    time: mid-run, after a gate, with nobody watching the one process that was supposed to be
    watching.
    """
    env.build(write_prompt=False)

    r = env.srv.get("/api/manifest")
    assert r.status_code == 422, r.text
    err = r.json()["error"]
    assert err["code"] == "invalid_manifest", err
    assert err["detail"]["pointer"] == "/roles/reviewer/prompt_file", err
    assert err["detail"]["path"].endswith(".factory/prompts/reviewer.md"), err
    assert real(env.root) in err["detail"]["path"], err

    # ...and the process is up and says so, which is what E1 is for.
    health = env.srv.get("/api/health")
    assert health.status_code == 200, health.text
    assert health.json()["manifest"] == "invalid", health.text


def test_no_seat_defers_rather_than_drops(env):
    """With every seat taken the role is QUEUED and the ticket says so — and it starts when a seat
    frees.

    Dropping it silently would rebuild the exact failure this feature exists to fix, one layer
    down: a check that does not happen and nothing anywhere saying it did not.
    """
    env.build(lanes_host=1)
    lane = env.claim("TCK-001")
    env.start(lane)
    env.wait_log(lane, "ready")

    env.report(lane, "GATE")

    deadline = time.time() + 10
    while time.time() < deadline:
        roles = env.ticket("TCK-001").get("roles")
        if roles:
            break
        time.sleep(0.05)
    assert roles == [{"role": "reviewer", "stage": "GATE", "state": "pending"}], roles
    assert len(env.lanes()) == 1, "a role started with no seat for it"

    env.release(lane)

    rows = env.wait_lanes(1)
    assert rows[0]["role"] == "reviewer", rows
    assert rows[0]["ticket_id"] == "TCK-001", rows
    env.wait_log(rows[0]["lane"], "msg<<<")

    started = env.ticket("TCK-001")["roles"]
    assert [r["state"] for r in started] == ["started"], started
    assert started[0]["lane"] == rows[0]["lane"], started
    assert started[0]["run_id"] == rows[0]["run_id"], started


# ── the mandatory negative ──────────────────────────────────────────────────────────────────

def test_a_role_cannot_trigger_itself(env):
    """NEGATIVE (mandatory). A role whose OWN run reports the stage that declares it does not
    start another.

    Left out, one misconfigured manifest spawns lanes until the seats run out, and the tool that
    was supposed to make the process reliable becomes the thing that takes the machine down. The
    seat is what turns that from unbounded into merely useless — so this case runs with ONE seat,
    where the failure is not a refused claim but a role sitting in the queue waiting to be handed
    the seat its own predecessor is about to give up.
    """
    env.build(lanes_host=1)
    lane = env.claim("TCK-001", role="reviewer")
    env.start(lane)
    env.wait_log(lane, "ready")

    env.report(lane, "GATE")

    # Nothing is queued, because nothing was admitted: a role does not start itself.
    assert env.ticket("TCK-001").get("roles") in (None, []), env.ticket("TCK-001")
    assert [r["lane"] for r in env.settle()] == [lane], env.lanes()

    # ...and the seat its own release frees does not become the next copy of it.
    env.release(lane)
    time.sleep(2.5)
    assert env.lanes() == [], (
        "the reviewer started a reviewer; with one seat that is a queue that refills itself "
        f"forever: {env.lanes()}")
    assert env.ticket("TCK-001").get("roles") in (None, []), env.ticket("TCK-001")


def test_a_role_is_never_started_against_a_ticket_its_branch_does_not_have(env):
    """SRV-FIX-060 QA, finding 1 — the SECOND call site of `api::prompt::values`.

    `SRV-FIX-060` taught E18 to refuse a start whose ticket is not on the lane's branch, and left
    `roles.cpp` — the other caller of the same function — building the same path in the same way
    with no check at all. §1 of that ticket said the answer must hold *"on both call sites"* and
    named `F31` in its Spec line; the fix landed at one, and QA reproduced the dead path here in
    one step.

    The step is this test's setup, verbatim: a person types a prompt into Start, which bypasses
    E18's refusal because a TYPED prompt carries no placeholders and needs no ticket. That run
    reports its stage. F31 then starts the role with the repository's OWN prompt file, which does
    carry `{ticket_path}` — and the role's branch is cut fresh from `HEAD`, so it has the ticket
    even less than the triggering lane does.

    What must happen: nothing is started, the lane claimed for it is given back, and the log says
    which ticket and which branch.
    """
    env.build(tickets=("TCK-001",))
    uncommitted = Path(env.root, "docs", "tickets", "TCK-099.md")
    uncommitted.write_text(
        "---\nid: TCK-099\nworkspace: core\nstatus: todo\nstages: SPEC GATE\n---\n\n# not committed\n")
    assert env.srv.get("/api/tickets/TCK-099").status_code == 200, "the catalog reads the worktree"

    lane = env.claim("TCK-099")
    env.start(lane, prompt="do the thing")          # a typed prompt — E18 has no ticket to check
    env.wait_log(lane, "ready")
    env.report(lane, "GATE")

    after = env.settle()
    assert [r["lane"] for r in after] == [lane], (
        "a role was started against a ticket its own branch does not have: "
        f"{[(r['lane'], r['role']) for r in after]}")

    # ...and the seat is not leaked. `abandon` after a successful claim used to keep the lane and
    # its worktree forever, which nobody had noticed because no role has ever run here.
    seats = env.srv.get("/api/lanes").json()["seats"]["host"]
    assert seats["used"] == 1, seats


def test_a_role_whose_ticket_is_committed_still_starts(env):
    """NEGATIVE (mandatory) for the case above. The refusal must not become "roles do not start".

    This is the ordinary path and the one every other F31 case depends on, asserted here on its
    own so a fix that refuses everything cannot hide behind them.
    """
    env.build(tickets=("TCK-001",))
    lane = env.claim("TCK-001")
    env.start(lane)
    env.wait_log(lane, "ready")
    env.report(lane, "GATE")

    rows = env.wait_lanes(2)
    started = [r for r in rows if r["lane"] != lane][0]
    assert started["role"] == "reviewer", started
    env.wait_log(started["lane"], "msg<<<")
    probes = [l for l in env.log(started["lane"]).splitlines()
              if l.startswith(("opened:", "unreadable:"))]
    assert probes and all(l.startswith("opened:") for l in probes), probes


def test_a_role_lane_reaches_the_ticket_it_is_about_with_no_reload(one):
    """SRV-FIX-080 — the journey the defect was found on.

    `test_a_declared_role_starts_on_its_stage` above asserts the lane EXISTS after the tool
    started it, by reading E9. Nothing asserted that anybody was told, and a person watching this
    ticket in a browser is not going to poll E9 — so for as long as M17 did not exist, the whole
    of F31 was invisible until a reload, on the one screen it was built for.
    """
    c = one.subscribed({"ticket": "TCK-001"})

    one.report(one.lane, "GATE")

    rows = one.wait_lanes(2)
    started = [r for r in rows if r["lane"] != one.lane][0]

    frames = one.frames(c, "lane.membership", seconds=5.0)
    mine = [f for f in frames if f["lane"] == started["lane"]]
    assert mine, (
        f"the tool claimed {started['lane']} for TCK-001 and told nobody; "
        f"the ticket's subscriber saw {[f.get('m') for f in frames]}")
    assert mine[0]["change"] == "claimed", mine[0]
    assert mine[0]["ticket_id"] == "TCK-001", mine[0]
    assert mine[0]["workspace"] == started["workspace"], (mine[0], started)


# ── SRV-FIX-123 · F31's start obeys E18's resource gate ──────────────────────────────────────
#
# `POST /api/lanes/{lane}/runs` refuses a lane still queued on somebody else's exclusive resource
# with `409 resource_busy`. The role start is the OTHER caller of that rule and did not apply it:
# it spawned the agent immediately, in a lane E9 reported as `queued` holding nothing. In THIS
# repository `board-master` is `needed_by: ["master"]` and `roles.qa.start_on_stage` is `GATE`, so
# a firmware lane at GATE holds the one ESP32 while the tool starts a second agent whose prompt
# tells it to re-run the ticket's own criteria — `./build.sh app flash log`. Root CLAUDE.md: "Two
# agents flashing the same board do not produce two failures — they produce one corrupted run."
#
# DEFER, not abandon. The other role-side refusals (an unreadable prompt, a ticket the branch does
# not have) are permanent: nothing about waiting makes them true. A held resource is temporary by
# construction — the holder releases it — and abandoning would throw away a checker that would have
# been right thirty seconds later. In this repository abandon is not merely wasteful but total:
# `board-master` is needed by `master`, QA starts on `GATE`, and a master lane at GATE ALWAYS holds
# the board, so abandon means the qa role never runs for any firmware ticket — the feature
# SRV-FEAT-065 exists to deliver, off by construction. Deferring costs one occupied seat, which is
# what a person already spends when they wait out a `409` by hand.
#
# The assertions below read the AGENT'S OWN LOG, never `state.process`. The lane row is the thing
# that already said `queued` while the process ran, so the row cannot be the witness.

RESOURCE = {"board-master": {"exclusive": True, "needed_by": ["core"],
                             "description": "one ESP32 on /dev/ttyUSB1"}}


def role_lane(env, *known, timeout=25):
    """The lane the TOOL claimed — the one nobody asked for."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        other = [r for r in env.lanes() if r["lane"] not in known]
        if other:
            return other[0]["lane"]
        time.sleep(0.05)
    raise AssertionError(
        f"no role lane appeared; there are {[(r['lane'], r['role']) for r in env.lanes()]}")


def test_a_role_is_not_started_while_the_resource_it_needs_is_held(env):
    """No process exists in the role's lane for as long as another lane holds the board."""
    env.build(resources=RESOURCE)
    lane = env.claim("TCK-001")
    assert env.row(lane)["resources_held"] == ["board-master"], env.row(lane)
    env.start(lane)
    env.wait_log(lane, "ready")

    env.report(lane, "GATE")
    role = role_lane(env, lane)

    env.settle()          # long enough that an agent which WAS going to start would have
    log = env.log(role)
    assert "argv=" not in log, log
    assert "ready" not in log, log
    assert "msg<<<" not in log, log
    assert env.row(lane)["resources_held"] == ["board-master"], "the holder still holds it"


def test_the_deferred_role_starts_when_the_resource_is_granted(env):
    """And when the holder lets go, the deferred role runs — `ready` strictly after the release."""
    env.build(resources=RESOURCE)
    lane = env.claim("TCK-001")
    env.start(lane)
    env.wait_log(lane, "ready")
    env.report(lane, "GATE")
    role = role_lane(env, lane)

    env.settle()
    before = env.log(role)
    assert "ready" not in before, before

    env.release(lane)

    after = env.wait_log(role, "ready")
    assert "ready" in after and "ready" not in before
    assert env.row(role)["resources_held"] == ["board-master"], env.row(role)
    assert "You are checking TCK-001." in after, after


def test_a_role_on_a_free_resource_still_starts_at_once(env):
    """NEGATIVE (mandatory) — the same declaration with the resource NOT held still starts at once.

    Goes red if the guard fires on the mere PRESENCE of a resources block rather than on the
    resource actually being held. `board-master` is declared and exclusive, but it is needed by a
    workspace no lane here is in, so nobody holds it and nothing may be deferred.
    """
    env.build(resources={"board-master": {"exclusive": True, "needed_by": ["side"],
                                          "description": "one ESP32 on /dev/ttyUSB1"}},
              workspaces={"core": {"path": "."}, "side": {"path": "."}})
    lane = env.claim("TCK-001")
    assert env.row(lane)["resources_held"] == [], env.row(lane)
    env.start(lane)
    env.wait_log(lane, "ready")

    env.report(lane, "GATE")
    role = role_lane(env, lane)

    # `ready` and the delivered prompt, not `argv=`: this repository's stand-in stopped echoing
    # its argv, and a case asserting a line the fixture no longer writes fails for a reason that
    # has nothing to do with the role starting. What the criterion is about is that the agent RAN
    # and was given its prompt — both of which this asserts, and both of which vanish if the start
    # is deferred.
    log = env.wait_log(role, "ready")
    assert "ready" in log, log
    assert "You are checking TCK-001." in log, log


def test_every_refusal_e18_lists_is_applied_to_the_role_caller_too():
    """SRV-FIX-123 §3's structural change — the class, read out of SPEC.md rather than remembered.

    `SRV-FIX-060` applied E18's rule to ONE refusal (`ticket_not_on_branch`) and `SRV-FIX-123` to a
    second (`resource_busy`), three months apart, because "the same refusal belongs to F31's start"
    lived in prose. A rule applied to one caller is not applied.

    So the table in `SPEC.md` E18 is the list, and this reads it: every refusal E18 names has a row,
    and every row says what the role caller does with it. A refusal added to the endpoint without a
    row turns this red — which is the point, since the row is where the next reader looks.
    """
    from pathlib import Path
    import re

    spec = (Path(__file__).resolve().parents[2] / "docs/product/SPEC.md").read_text()
    e18 = spec[spec.index("#### E18"):spec.index("#### E19")]

    start = e18.index("| E18's refusal | the role caller |")
    # the table is indented inside a bullet, so strip before matching the pipe
    rows = [l.strip() for l in e18[start:].splitlines()
            if l.strip().startswith("|") and "---" not in l][1:]
    assert rows, "the E18 refusal table has no rows"

    # every refusal E18's Missing paragraph names, and the answer the table gives it
    missing = e18[e18.index("- **Missing**"):]
    named = set(re.findall(r"`(4\d\d [a-z_]+|5\d\d [a-z_]+)`", missing))
    named = {n.split()[1] for n in named}
    assert named, "E18's Missing paragraph names no refusal codes"

    covered = " ".join(rows)
    for code in sorted(named):
        assert code in covered, (
            f"E18 refuses with `{code}` and the role-caller table has no row for it. A refusal "
            "applied to one of two callers is not applied — SRV-FIX-060 and SRV-FIX-123 are the "
            "same defect three months apart, both because this list was prose.")

    for row in rows:
        answer = row.split("|")[2].lower()
        assert any(w in answer for w in ("defer", "abandon", "cannot occur")), (
            f"this row names no role-side answer: {row.strip()[:90]}")


# ── SRV-FIX-184 · a role judges the work, so a role lane is cut from the branch the work is on ─
#
# `lane::git::addWorktree` created every lane with `git worktree add -b <branch> <path> HEAD`, and
# `HEAD` is the MAIN CHECKOUT's commit. Right for a `run` lane — the work starts from where the
# repository is — and wrong for a role, which exists to look at commits the run lane made. Every
# case here forces the two sources apart before it claims the role; see the note at the top of this
# file for why a fixture where they agree cannot tell which one the code used.


def rev(at, ref):
    """The commit a ref names, asked of `at` — a checkout, any checkout of the repository."""
    return git(at, "rev-parse", ref).stdout.strip()


def contains(at, commit, ref):
    """Is `commit` an ancestor of `ref` — the ticket's own measurement, verbatim."""
    return git(at, "merge-base", "--is-ancestor", commit, ref, check=False).returncode == 0


def commit_in(worktree, name, text, message):
    """Write a file that exists nowhere else and commit it, in that worktree's own branch."""
    (Path(worktree) / name).write_text(text)
    git(worktree, "add", "-A")
    git(worktree, "commit", "-m", message)
    return rev(worktree, "HEAD")


def move_head(root, message="FIX-081: VERIFY — the merged thing works on main"):
    """A concurrent session commits to the main checkout. This is how the defect was found."""
    (Path(root) / "another-session.txt").write_text(message + "\n")
    git(root, "add", "-A")
    git(root, "commit", "-m", message)
    return rev(root, "HEAD")


THE_WORK = "the work the role exists to judge\n"


def test_a_role_lane_is_cut_from_the_run_lanes_branch(env):
    """The measurement, as a fixture: a run lane commits, and the role lane claimed for the same
    ticket CONTAINS that commit and can read the file.

    Before this ticket the role branch was cut from `HEAD` and had neither — `SRV-FIX-174`'s QA
    lane carried `f096c936` nowhere in its history, and a QA session there re-ran every §1
    criterion against unmodified code.
    """
    env.build()
    run_lane = env.claim("TCK-001")
    work = commit_in(env.worktree(run_lane), "only-in-the-run-lane.txt", THE_WORK,
                     "the work under review")

    role_lane = env.claim("TCK-001", role="qa")

    assert contains(env.root, work, "ticket/TCK-001-qa"), (
        f"the role lane does not contain the work it exists to judge: {work} is not an ancestor "
        f"of ticket/TCK-001-qa, which is at {rev(env.root, 'ticket/TCK-001-qa')}\n"
        + git(env.root, "log", "--oneline", "-3", "ticket/TCK-001-qa").stdout)

    read_from_the_role_lane = env.worktree(role_lane) / "only-in-the-run-lane.txt"
    assert read_from_the_role_lane.is_file(), (
        f"{read_from_the_role_lane} is not there; a role that cannot open the work cannot judge it")
    assert read_from_the_role_lane.read_text() == THE_WORK


def test_head_moves_under_the_claim_and_the_role_still_follows_the_work(env):
    """THE case: the two candidate bases hold different commits, and the role takes the work's.

    A concurrent session commits to the main checkout between the run lane's commit and the role
    claim — exactly what happened on 2026-08-21 — so `HEAD` and `ticket/TCK-001` disagree. Only a
    fixture where they disagree can say which one the code read.
    """
    env.build()
    run_lane = env.claim("TCK-001")
    work = commit_in(env.worktree(run_lane), "only-in-the-run-lane.txt", THE_WORK,
                     "the work under review")

    unrelated = move_head(env.root)
    assert unrelated != work, "the fixture failed to force the two sources apart"
    assert not contains(env.root, work, "HEAD"), \
        "the main checkout carries the run lane's commit; the two sources have not been separated"

    role_lane = env.claim("TCK-001", role="qa")
    role_branch = "ticket/TCK-001-qa"

    assert contains(env.root, work, role_branch), (
        f"the role lane was cut from HEAD, not from the work: {role_branch} is at "
        f"{rev(env.root, role_branch)}\n"
        + git(env.root, "log", "--oneline", "-3", role_branch).stdout)
    assert not contains(env.root, unrelated, role_branch), (
        f"the role lane carries another session's commit {unrelated}, which has nothing to do "
        f"with this ticket:\n" + git(env.root, "log", "--oneline", "-3", role_branch).stdout)
    assert not (env.worktree(role_lane) / "another-session.txt").exists(), \
        "the other session's file is in the tree the role was started to judge"


def test_a_role_lane_with_no_run_lane_is_still_cut_from_head(env):
    """A ticket with no run lane has no work to follow, so `HEAD` is the honest base.

    A repository that uses roles that way is not changed by this ticket — the narrowing is one
    case, not a redesign of where lanes come from.
    """
    env.build()
    head = move_head(env.root)          # so "cut from HEAD" is a claim about a specific commit

    role_lane = env.claim("TCK-002", role="qa")

    assert not branch_exists(env.root, "ticket/TCK-002"), \
        "the claim invented a run branch; there is no run lane for TCK-002"
    assert rev(env.root, "ticket/TCK-002-qa") == head, (
        f"a role with no run lane was cut from {rev(env.root, 'ticket/TCK-002-qa')} rather than "
        f"from HEAD at {head}")
    assert (env.worktree(role_lane) / "another-session.txt").is_file()


def test_the_role_lane_is_still_its_own_branch_and_worktree(env):
    """`SRV-FEAT-065`'s independence, held. Following the work is not sharing a checkout.

    Its own branch, named for the role; its own directory; its own session. What it is no longer
    independent of is the commits it was started to look at — and that is the only thing this
    ticket changes.
    """
    env.build()
    run_lane = env.claim("TCK-001")
    run_wt = env.worktree(run_lane)
    commit_in(run_wt, "only-in-the-run-lane.txt", THE_WORK, "the work under review")
    move_head(env.root)

    role_lane = env.claim("TCK-001", role="qa")
    role_wt = env.worktree(role_lane)
    run_row, role_row = env.row(run_lane), env.row(role_lane)

    assert role_row["branch"] == "ticket/TCK-001-qa", role_row
    assert run_row["branch"] == "ticket/TCK-001", run_row
    assert real(role_wt) != real(run_wt), (role_row, run_row)

    # ...and the role's worktree is ON its own branch — not a second checkout of the run branch.
    assert git(role_wt, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "ticket/TCK-001-qa"
    assert git(run_wt, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "ticket/TCK-001"

    # ...and a session of its own, which is what "a role is a different session" is implemented as.
    env.start(run_lane)
    env.start(role_lane)
    env.wait_log(run_lane, "ready")
    env.wait_log(role_lane, "ready")
    run_row, role_row = env.row(run_lane), env.row(role_lane)
    assert role_row["session_id"], role_row
    assert role_row["session_id"] != run_row["session_id"], (role_row, run_row)
    assert role_row["run_id"] != run_row["run_id"], (role_row, run_row)


# ── the mandatory negatives ──────────────────────────────────────────────────────────────────

def test_committing_in_a_role_lane_does_not_move_the_run_branch(env):
    """NEGATIVE (mandatory). The cheap way to make a role lane contain the work is to check the
    RUN BRANCH out in the role's worktree.

    That satisfies every assertion above — the work is in the tree, it is readable — and it hands
    the role write access to the branch it is judging: its verdict commits land on the work, and a
    role can rewrite what it was asked to look at. Branching FROM the run branch and checking it
    OUT are the same tree and different histories, and only a commit tells them apart.

    Asserted on the sequence in which the cheap fix SURVIVES: the run lane is released first, which
    frees its seat and keeps its branch (E13), so git no longer refuses a second checkout. That is
    also the normal way a role starts, so this is the ordinary path and not a corner.
    """
    env.build()
    run_lane = env.claim("TCK-001")
    work = commit_in(env.worktree(run_lane), "only-in-the-run-lane.txt", THE_WORK,
                     "the work under review")
    move_head(env.root)

    env.release(run_lane)
    assert branch_exists(env.root, "ticket/TCK-001"), "E13 kept no branch; the fixture is wrong"
    assert rev(env.root, "ticket/TCK-001") == work

    role_lane = env.claim("TCK-001", role="qa")
    role_wt = env.worktree(role_lane)

    # ...and a RELEASED run lane still counts — the work it left on its branch is here.
    assert (role_wt / "only-in-the-run-lane.txt").is_file(), (
        "the role lane claimed after the run lane was released does not contain the work: "
        + git(env.root, "log", "--oneline", "-3", "ticket/TCK-001-qa").stdout)

    verdict = commit_in(role_wt, "verdict.md", "PASS\n", "the role's verdict")

    assert rev(env.root, "ticket/TCK-001") == work, (
        f"the role's commit moved the branch under review: ticket/TCK-001 was {work} and is now "
        f"{rev(env.root, 'ticket/TCK-001')}\n"
        + git(env.root, "log", "--oneline", "-3", "ticket/TCK-001").stdout)
    assert rev(env.root, "ticket/TCK-001-qa") == verdict, \
        "the role's own branch did not take its commit"
    assert git(role_wt, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "ticket/TCK-001-qa", \
        "the role lane is standing on the branch it is judging, not on one of its own"


def test_a_role_for_an_unknown_ticket_is_still_refused(env):
    """NEGATIVE (mandatory). Resolving a base must not turn a refusal into a lane.

    Every refusal `E12` already makes is unchanged by this ticket: an unknown ticket and an unknown
    workspace are `404`, a second lane in the same role is `409 lane_exists`, and none of them
    leaves a branch or a worktree behind.
    """
    env.build()

    r = env.srv.post("/api/lanes", json={"ticket_id": "TCK-404", "workspace": "core", "role": "qa"})
    assert r.status_code == 404, r.text
    assert not branch_exists(env.root, "ticket/TCK-404-qa"), \
        "a refused claim created a branch"
    assert not branch_exists(env.root, "ticket/TCK-404")

    r = env.srv.post("/api/lanes", json={"ticket_id": "TCK-001", "workspace": "nope", "role": "qa"})
    assert r.status_code == 404, r.text

    assert env.lanes() == [], env.lanes()

    # ...and the refusal that IS about a role: one lane per (ticket, role), still 409.
    role_lane = env.claim("TCK-001", role="qa")
    again = env.srv.post("/api/lanes",
                         json={"ticket_id": "TCK-001", "workspace": "core", "role": "qa"})
    assert again.status_code == 409, again.text
    assert again.json()["error"]["code"] == "lane_exists", again.text
    assert [r["lane"] for r in env.lanes()] == [role_lane], env.lanes()


# ── SRV-FEAT-065 round 4 · §1's criteria, under §1's own names ────────────────────────────────
#
# Rounds 1 and 3 both recorded criteria 1, 3 and 5 as "name does not exist" / "wrong file" and
# then PASSED them on substance under whatever name had shipped. Three QA sessions spent their
# first move mapping §1's names onto the suite's. The names below are §1's, verbatim, so a fourth
# does not have to. Where the substance was already there, the case says so and the report carries
# it as a finding — "already true" is an answer, and it is the one §1 asked for in criterion 3.


def test_a_qa_lane_checks_out_the_branch_it_is_judging(env):
    """§1's criterion 1, by the path §1 names: a lane that REPORTS A STAGE, not one claimed by hand.

    `SRV-FIX-184` asserts this for `E12` — a role lane claimed directly — and that is the same
    property one layer down. The path this ticket is about is the other one: the tool claiming a
    lane **nobody asked for**, from `api::roles.cpp`, on a stage report. A fix asserted only at the
    call site a person uses is a fix nobody has checked at the call site the feature adds.

    The two candidate bases are forced apart before the role starts, per this file's opening note:
    the work is committed in the run lane's worktree and `HEAD` is moved afterwards, so a role cut
    from `HEAD` carries the concurrent session's commit and not the work.
    """
    env.build(roles={"qa": {"start_on_stage": "GATE", "lane": "new",
                            "prompt_file": ".factory/prompts/reviewer.md"}})
    lane = env.claim("TCK-001")
    env.start(lane)
    env.wait_log(lane, "ready")

    work = commit_in(env.worktree(lane), "only-in-the-run-lane.txt", THE_WORK, "the work under review")
    moved = move_head(env.root)
    assert work != moved

    env.report(lane, "GATE")
    started = [r for r in env.wait_lanes(2) if r["lane"] != lane][0]
    assert started["role"] == "qa", started
    # A lane is a real lane from the instant it is RESERVED, and its worktree is built after that
    # (`test_the_started_lane_cannot_see_the_triggering_one` says so for the same reason). Asserting
    # on git state before the started run has spoken measures a directory that is still being made.
    env.wait_log(started["lane"], "ready")

    # Asserted on `git log` in the QA worktree, which is §1's own instruction — the branch NAME
    # was right the whole time the content was wrong.
    worktree = Path(env.row(started["lane"])["worktree"])
    assert contains(worktree, work, "HEAD"), (
        f"the started qa lane does not contain the work it was started to judge: {work} is not an "
        f"ancestor of its HEAD.\n" + git(worktree, "log", "--oneline", "-4").stdout)
    assert not contains(worktree, moved, "HEAD"), (
        f"the started qa lane carries {moved}, a concurrent session's commit on the main checkout "
        f"— it was cut from HEAD.\n" + git(worktree, "log", "--oneline", "-4").stdout)
    assert (worktree / "only-in-the-run-lane.txt").read_text() == THE_WORK


def test_the_qa_lane_can_write_its_verdict(one):
    """§1's criterion 2, as §1 words it: *a file write inside the lane COMPLETES, un-answered by
    any human* — **assert the file exists on disk**.

    This is not an assertion about a CLI permission mode and must not be read as one: the stand-in
    is a shell script, for which `--permission-mode` means nothing. That half was measured against
    the real CLI instead and is recorded in
    `factory/docs/reports/SRV-FEAT-065/SRV-FEAT-065-cli-permission-mode-2026-08-05.md` — under
    `auto`, the mode every lane runs, the write COMPLETES.

    What can genuinely fail here is the half that is the tool's: a run the tool started, in a
    worktree the tool made, under the confinement the tool imposes, puts a file on disk and it is
    still there, with nobody watching. `SRV-FEAT-002`'s sandbox refuses writes outside the lane; one
    that also refused them inside would make a checker that reaches a verdict and cannot record it.
    """
    one.report(one.lane, "GATE")
    started = [r for r in one.wait_lanes(2) if r["lane"] != one.lane][0]
    one.wait_log(started["lane"], "ready")

    worktree = Path(started["worktree"])
    written = worktree / "agent-log.txt"
    assert written.is_file(), sorted(p.name for p in worktree.iterdir())
    assert "ready" in written.read_text(), written.read_text()
    # ...INSIDE the lane, not somewhere that merely happens to be writable
    assert written.resolve().is_relative_to(worktree.resolve()), (written, worktree)

    # ...and nobody answered anything, asserted on a field that EXISTS.
    #
    # SRV-FEAT-065 round 4 QA, F2: this ended by reading `GET /api/permissions` for pending
    # requests. There is no such route — `manager.h` has `/api/permissions/rules`, `/policy`,
    # `/decisions` and `POST /api/permission` — so it answered `404 {"error":"not found"}`,
    # `conftest`'s `get` does not raise, and `.get("requests", [])` was `[]` on every run. The
    # assertion was green whatever the server did, which is the one thing an assertion may not be.
    #
    # `state.blocked` is E9's own field for it, filled from `permissions::Store::blockedSnapshot`
    # (`api/lane-view.cpp:133`): non-null exactly when a lane is stopped waiting for a human to
    # answer a tool call. A run that had to ask would be `process: "blocked"` with a reason here.
    row = one.row(started["lane"])
    assert row["state"]["blocked"] is None, (
        f"the started run is waiting for a human to answer it: {row['state']}")
    assert row["state"]["process"] != "blocked", row["state"]


def test_a_qa_lane_does_not_see_the_lane_it_is_judging(one):
    """§1's criterion 5, the mandatory negative — and BOTH halves of the word it turns on.

    §1 asks that the started run's transcript, session id and working directory share nothing with
    the triggering run. That half was built and asserted by `SRV-FEAT-022`
    (`test_the_started_lane_cannot_see_the_triggering_one`) and was green before this ticket opened;
    round 3's QA was right to record that as a limitation, and this case does not pretend otherwise.

    What it adds is the half `docs/operations/ESCAPES.md` (2026-08-21) says the word *independence* hid: a QA
    lane must be independent of the SESSION and must NOT be independent of the WORK. Asserting only
    the first is how a lane came to be cut from `HEAD` and judge unmodified code with every
    separation criterion green. The two are asserted together here because apart they are the
    defect.
    """
    work = commit_in(one.worktree(one.lane), "the-fix.txt", THE_WORK, "the fix under review")
    one.report(one.lane, "GATE")
    started = [r for r in one.wait_lanes(2) if r["lane"] != one.lane][0]
    one.wait_log(started["lane"], "ready")

    # ── independent of the session ── §1's three: working directory, session id, transcript.
    #
    # The triggering RUN's id is deliberately NOT among them: `{triggered_by_run}` is one of F31's
    # four declared placeholders, so the repository's own prompt asks for it by name. Forbidding it
    # here would assert against the specification. `test_the_started_lane_cannot_see_the_triggering_one`
    # counts its occurrences against the prompt instead, which is the right treatment and is not
    # duplicated here.
    mine = one.row(one.lane)
    started = one.row(started["lane"])
    assert started["worktree"] != mine["worktree"], started
    cwd = [l for l in one.log(started["lane"]).splitlines() if l.startswith("cwd=")]
    assert cwd and real(cwd[0].split("=", 1)[1]) == real(started["worktree"]), (cwd, started)
    assert started["session_id"] and started["session_id"] != mine["session_id"], (started, mine)
    assert one.lane not in one.log(started["lane"]), one.log(started["lane"])
    assert mine["session_id"] not in one.log(started["lane"]), one.log(started["lane"])
    assert mine["session_id"] not in json.dumps(one.transcript(started["run_id"]))

    # ── and NOT independent of the work ──
    assert contains(Path(started["worktree"]), work, "HEAD"), (
        "the qa lane shares nothing with the triggering lane — including the work it exists to "
        "judge, which is the half that must not be true.\n"
        + git(Path(started["worktree"]), "log", "--oneline", "-4").stdout)
# ── SRV-FIX-189 · THE SECOND CLAIM ───────────────────────────────────────────────────────────
#
# `SRV-FIX-184`, one round later. It made `lane::Store::reserve_` resolve `ticket/<id>` and hand
# it to `addWorktree` as the base, and that holds — for the FIRST claim of a role. `addWorktree`
# has two paths, and only one of them consults the base:
#
#     if (branchExists(repoRoot, branch))
#         return run(repoRoot, {"worktree", "add", path, branch});     // <- base discarded
#     return run(repoRoot, {"worktree", "add", "-b", branch, path, base});
#
# `E13` always keeps the branch, so EVERY claim after the first takes the top path. The round
# after a QA FAIL is a role lane claimed for the second time, judging a run branch that has moved:
# it read the round-1 tree, watched the RED test the implementer had just written go red, and
# failed the ticket a second time for work that was already done. The inverse is quieter — a
# criterion asserting an invariant round 1 already held goes green, and `status: pass`, which only
# a role may set, is set from a tree the fix is not on.
#
# So, beside the note at the top of this file: any fixture that asserts anything about what a role
# lane CONTAINS **claims the role TWICE, with a commit on the run branch in between**. Claiming it
# once measures the create path and says nothing about the one every real round takes.


def head_of(worktree):
    return git(worktree, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def two_rounds(env, *, contested=None):
    """The sequence `docs/TICKET-WORKFLOW.md` prescribes after a QA FAIL, as a fixture.

    Run lane, round-1 work, role lane, the role's verdict, the role lane released to free its seat
    (`E13` keeps its branch), then the round-2 commit on the run branch. Returns everything the
    cases below measure; the role is NOT re-claimed here, because what the re-claim answers is the
    thing under test.

    `contested`, when given, is a file every commit rewrites, so the two histories genuinely
    conflict rather than merely diverge.
    """
    env.build()
    run_lane = env.claim("TCK-001")
    run_wt = env.worktree(run_lane)
    if contested:
        (run_wt / contested).write_text("round 1\n")
    work1 = commit_in(run_wt, "only-in-the-run-lane.txt", THE_WORK, "round 1: the work")

    # The two candidate bases, forced apart — the note at the top of this file.
    move_head(env.root)

    role_lane = env.claim("TCK-001", role="qa")
    role_wt = env.worktree(role_lane)
    assert (role_wt / "only-in-the-run-lane.txt").is_file(), \
        "the FIRST claim did not follow the work; SRV-FIX-184's own criterion is red"
    if contested:
        (role_wt / contested).write_text("the role's own edit of the contested file\n")
    verdict = commit_in(role_wt, "verdict-round-1.md", "FAIL\n", "QA verdict round 1 — FAIL")

    env.release(role_lane)
    assert branch_exists(env.root, "ticket/TCK-001-qa"), "E13 kept no branch; the fixture is wrong"

    if contested:
        (run_wt / contested).write_text("round 2: the implementer's answer\n")
    work2 = commit_in(run_wt, "the-fix.txt", "the RED test and the fix QA asked for\n",
                      "round 2: the fix QA asked for")
    assert work2 != work1

    return dict(run_lane=run_lane, run_wt=run_wt, role_lane=role_lane,
                work1=work1, work2=work2, verdict=verdict)


def test_a_re_claimed_role_lane_contains_the_round_it_must_judge(env):
    """THE case. The role is claimed a second time and its worktree holds round 2.

    The transcript in `SRV-FIX-189` §1 is the current answer and it is `NO` on both lines: the
    round-2 commit is not an ancestor of the role's branch, and the fix file is not readable in
    the role's tree.
    """
    f = two_rounds(env)

    role_lane = env.claim("TCK-001", role="qa")
    role_wt = env.worktree(role_lane)

    assert contains(env.root, f["work2"], "ticket/TCK-001-qa"), (
        f"the round-2 role lane does not contain the round it exists to judge: {f['work2']} is "
        f"not an ancestor of ticket/TCK-001-qa, which is at "
        f"{rev(env.root, 'ticket/TCK-001-qa')}\n"
        + git(env.root, "log", "--oneline", "-4", "ticket/TCK-001-qa").stdout)
    assert (role_wt / "the-fix.txt").is_file(), (
        f"{role_wt / 'the-fix.txt'} is not there; the tree under review has no fix in it\n"
        + git(env.root, "log", "--oneline", "-4", "ticket/TCK-001-qa").stdout)
    assert (role_wt / "the-fix.txt").read_text() == "the RED test and the fix QA asked for\n"

    # ...and the round-1 work is still there too: a merge, not a replacement.
    assert (role_wt / "only-in-the-run-lane.txt").read_text() == THE_WORK
    # ...and still none of the concurrent session's commits (SRV-FIX-184, re-asserted here).
    assert not (role_wt / "another-session.txt").exists(), \
        "the other session's file is in the tree the role was started to judge"


def test_a_re_claimed_role_lane_still_has_its_own_earlier_commits(env):
    """The evidence a release exists to preserve survives the re-claim.

    A fix that deletes the role branch and cuts it again, or resets it to the run branch, passes
    the case above while destroying the round-1 verdict — so this is asserted beside it and not
    instead of it.
    """
    f = two_rounds(env)

    role_lane = env.claim("TCK-001", role="qa")
    role_wt = env.worktree(role_lane)

    assert (role_wt / "verdict-round-1.md").is_file(), (
        "the round-1 verdict is gone from the re-claimed role lane: "
        + git(env.root, "log", "--oneline", "-4", "ticket/TCK-001-qa").stdout)
    assert (role_wt / "verdict-round-1.md").read_text() == "FAIL\n"
    assert contains(env.root, f["verdict"], "ticket/TCK-001-qa"), (
        f"the role's own commit {f['verdict']} is no longer in its branch's history\n"
        + git(env.root, "log", "--oneline", "-4", "ticket/TCK-001-qa").stdout)
    # ...and it is still the role's own branch, not a second checkout of the one it judges.
    assert head_of(role_wt) == "ticket/TCK-001-qa", head_of(role_wt)


def test_the_run_branch_is_unmoved_by_the_re_claim(env):
    """NEGATIVE (mandatory, SRV-FIX-184's first, re-asserted on the path it did not cover).

    Bringing the work to the role must not push the role's commits back onto the branch under
    review. `ticket/TCK-001` points at round 2 before the re-claim and at round 2 after it, and
    the role's verdict is not in its history.
    """
    f = two_rounds(env)
    before = rev(env.root, "ticket/TCK-001")
    assert before == f["work2"]

    role_lane = env.claim("TCK-001", role="qa")

    assert rev(env.root, "ticket/TCK-001") == before, (
        f"the re-claim moved the branch under review: ticket/TCK-001 was {before} and is now "
        f"{rev(env.root, 'ticket/TCK-001')}\n"
        + git(env.root, "log", "--oneline", "-4", "ticket/TCK-001").stdout)
    assert not contains(env.root, f["verdict"], "ticket/TCK-001"), (
        f"the role's verdict {f['verdict']} landed on the branch it is judging\n"
        + git(env.root, "log", "--oneline", "-4", "ticket/TCK-001").stdout)
    assert not (f["run_wt"] / "verdict-round-1.md").exists(), \
        "the role's verdict file is in the run lane's tree"
    # ...and the run lane is still standing on its own branch.
    assert head_of(f["run_wt"]) == "ticket/TCK-001"
    # ...and the role really did take the work, so this is not green by doing nothing at all.
    assert (env.worktree(role_lane) / "the-fix.txt").is_file()


def test_a_re_claimed_role_with_no_new_work_is_unchanged(env):
    """The ordinary re-claim, undisturbed: released and claimed again with nothing new on the run
    branch leaves the role's branch on exactly the commit it was on.

    Not an empty merge commit, not a reset — the same sha.
    """
    env.build()
    run_lane = env.claim("TCK-001")
    commit_in(env.worktree(run_lane), "only-in-the-run-lane.txt", THE_WORK, "round 1: the work")
    move_head(env.root)

    role_lane = env.claim("TCK-001", role="qa")
    verdict = commit_in(env.worktree(role_lane), "verdict-round-1.md", "FAIL\n", "QA verdict")
    env.release(role_lane)
    assert rev(env.root, "ticket/TCK-001-qa") == verdict

    again = env.claim("TCK-001", role="qa")

    assert rev(env.root, "ticket/TCK-001-qa") == verdict, (
        f"a re-claim with no new work moved the role's branch from {verdict} to "
        f"{rev(env.root, 'ticket/TCK-001-qa')}\n"
        + git(env.root, "log", "--oneline", "-4", "ticket/TCK-001-qa").stdout)
    assert (env.worktree(again) / "verdict-round-1.md").read_text() == "FAIL\n"
    assert head_of(env.worktree(again)) == "ticket/TCK-001-qa"


def test_a_re_claim_that_cannot_take_the_work_refuses_rather_than_lying(env):
    """NEGATIVE (mandatory). The two histories genuinely conflict, and the claim says so.

    An implementation that swallows the merge failure hands back a lane whose tree is neither
    round — half of round 2 applied over round 1, with conflict markers in a file, and nothing on
    screen saying so. That is worse than the stale tree this ticket is about, because it looks
    current. So: an error naming the lane, the base and git's own conflict text; the role's branch
    exactly where it was; no lane, and no worktree left behind for the next claim to adopt.
    """
    f = two_rounds(env, contested="contested.txt")
    role_branch_was = rev(env.root, "ticket/TCK-001-qa")
    run_branch_was = rev(env.root, "ticket/TCK-001")

    r = env.srv.post("/api/lanes",
                     json={"ticket_id": "TCK-001", "workspace": "core", "role": "qa"})

    assert r.status_code == 409, f"the claim did not refuse: {r.status_code} {r.text[:400]}"
    body = r.json()
    assert body["error"]["code"] == "work_conflicts", r.text
    detail = body["error"]["detail"]
    assert detail["base"] == "ticket/TCK-001", detail
    assert "contested.txt" in detail["git"], detail
    assert detail["lane"].startswith("lane/"), detail

    assert rev(env.root, "ticket/TCK-001-qa") == role_branch_was, (
        f"the refused claim moved the role's branch from {role_branch_was} to "
        f"{rev(env.root, 'ticket/TCK-001-qa')}\n"
        + git(env.root, "log", "--oneline", "-4", "ticket/TCK-001-qa").stdout)
    assert rev(env.root, "ticket/TCK-001") == run_branch_was
    assert [row["lane"] for row in env.lanes()] == [f["run_lane"]], env.lanes()
    assert not Path(detail["path"]).exists(), (
        f"{detail['path']} was left behind: the next claim adopts it, and its tree is neither "
        "round")


def test_a_run_lane_re_claimed_is_never_rebased_on_anything(env):
    """NEGATIVE (mandatory). Only ROLE lanes follow work.

    A run lane is claimed, released and claimed again with `HEAD` moved in between: its branch is
    the commit it was left on, its tree holds nothing it did not put there, and its history is
    exactly its own commits. An implementation that applies the new base to every lane instead of
    to roles acquires the other session's commit here — the tool rewriting somebody's branch
    between claims, which is what `E13` keeping the branch exists to prevent.
    """
    env.build()
    run_lane = env.claim("TCK-001")
    work = commit_in(env.worktree(run_lane), "only-in-the-run-lane.txt", THE_WORK, "the work")
    env.release(run_lane)

    unrelated = move_head(env.root)
    assert not contains(env.root, unrelated, "ticket/TCK-001"), "the fixture failed to diverge"

    again = env.claim("TCK-001")
    wt = env.worktree(again)

    assert rev(env.root, "ticket/TCK-001") == work, (
        f"the re-claimed run lane's branch moved from {work} to "
        f"{rev(env.root, 'ticket/TCK-001')}\n"
        + git(env.root, "log", "--oneline", "-4", "ticket/TCK-001").stdout)
    assert not contains(env.root, unrelated, "ticket/TCK-001"), (
        f"the re-claimed run lane acquired {unrelated}, a commit of another session\n"
        + git(env.root, "log", "--oneline", "-4", "ticket/TCK-001").stdout)
    assert not (wt / "another-session.txt").exists(), \
        "the re-claimed run lane's tree holds a file it never put there"
    assert (wt / "only-in-the-run-lane.txt").read_text() == THE_WORK
    assert head_of(wt) == "ticket/TCK-001"


# ── SRV-FIX-256 · a released role lane can be started again ───────────────────────────────────
#
# `SRV-FEAT-065` ends at *"it is stopped from the browser"*. This is the next second: releasing the
# checker's lane leaves its `(ticket, stage, role)` key in `roles_`, so `requestRole` refuses for
# ever afterwards. Stop, Kill and Release — the three controls that ticket exists to give — are a
# one-way door, and nothing on any screen says so. The implementing session's later reports are
# accepted with `202` and do nothing.

def test_a_released_role_lane_can_be_started_again_by_a_report(one):
    """Release the checker, report the stage again, and a NEW checker lane appears.

    One server process throughout: a fresh one cannot fail this, because the whole defect is that
    the release does not forget.
    """
    work = commit_in(one.worktree(one.lane), "the-work.txt", THE_WORK, "the work under review")

    one.report(one.lane, "GATE")
    first = [r for r in one.wait_lanes(2) if r["lane"] != one.lane][0]
    one.wait_log(first["lane"], "ready")
    assert first["role"] == "qa" or first["role"] == "reviewer", first

    one.release(first["lane"])
    assert [r["lane"] for r in one.wait_lanes(1)] == [one.lane]

    one.report(one.lane, "GATE")
    second = [r for r in one.wait_lanes(2) if r["lane"] != one.lane][0]
    one.wait_log(second["lane"], "ready")
    assert second["role"] == first["role"], second

    # ...and it is cut from the branch under judgement, asserted on `git log` in its worktree.
    worktree = Path(one.row(second["lane"])["worktree"])
    assert contains(worktree, work, "HEAD"), (
        "the restarted checker does not contain the work it exists to judge:\n"
        + git(worktree, "log", "--oneline", "-3").stdout)


def test_the_roles_row_never_names_a_lane_that_is_gone(one):
    """After the release, no role is reported `started` at a lane `E9` no longer lists.

    Asserted across the two endpoints a screen actually reads — the ticket and the lane list — not
    on an internal member. The row is a clickable control; pointing it at a lane that does not
    exist is the screen lying.
    """
    one.report(one.lane, "GATE")
    started = [r for r in one.wait_lanes(2) if r["lane"] != one.lane][0]
    one.wait_log(started["lane"], "ready")
    one.release(started["lane"])
    one.wait_lanes(1)

    live = {r["lane"] for r in one.lanes()}
    roles = one.ticket("TCK-001").get("roles", [])
    dangling = [r for r in roles if r.get("state") == "started" and r.get("lane") not in live]
    assert not dangling, (
        f"the ticket reports a started role at a lane that is gone: {dangling}; live lanes {live}")


def test_one_report_still_starts_exactly_one_lane(one):
    """MANDATORY NEGATIVE — the guard the fix must not drop.

    Two reports in quick succession WHILE the role lane is alive still produce exactly one. The
    cheap fix for the case above is to stop recording the key at all, which starts a second checker
    on every repeated report — the thing `already_started` exists to prevent.
    """
    one.report(one.lane, "GATE")
    rows = one.wait_lanes(2)
    started = [r for r in rows if r["lane"] != one.lane][0]
    one.wait_log(started["lane"], "ready")

    one.report(one.lane, "GATE")
    after = one.settle()
    assert len(after) == 2, (
        f"a repeated report while the checker is alive started another lane: "
        f"{[(r['lane'], r['role']) for r in after]}")


# ── SRV-FIX-256 §3 · the audit: what does a lane-keyed record say after the lane is gone ───────
#
# `_TEMPLATE.md` §2 gains **a record that names a lane is asked, in a test, what it says after that
# lane is released.** Three records in this server store a lane name; this is that question put to
# each of them, in one place, so the next one somebody adds has an obvious neighbour to copy.
#
#   roles_          — this ticket. Fixed: the release forgets.
#   sessionByLane_  — SRV-FIX-247, todo. Pinned below, not fixed here.
#   runCountByLane_ — the same class, pinned below.
#
# The two that are not fixed are PINNED rather than skipped: they assert today's behaviour and name
# the ticket, so whoever fixes them must come here and flip the assertion deliberately.

def test_a_record_that_names_a_lane_says_nothing_after_the_lane_is_released(one):
    """`roles_` — this ticket's own record, asked the question after the release."""
    one.report(one.lane, "GATE")
    started = [r for r in one.wait_lanes(2) if r["lane"] != one.lane][0]
    one.wait_log(started["lane"], "ready")
    one.release(started["lane"])
    one.wait_lanes(1)

    roles = one.ticket("TCK-001").get("roles", [])
    assert not [r for r in roles if r.get("lane") == started["lane"]], (
        f"the role record still names the released lane {started['lane']}: {roles}")


def test_the_run_counter_still_names_a_released_lane(env):
    """PINNED, not fixed — `runCountByLane_` is never erased (`agent/session.h:214`).

    A lane name is recycled: release `lane/01` and the next claim is `lane/01` again. The counter
    still holds the previous occupant's total, so the first run of the NEW occupant is announced
    with the old lane's count. The number a person reads is then about two pieces of work.

    Asserted as it behaves TODAY so that fixing it is a deliberate act with this case in the diff.
    Same class as `SRV-FIX-247` (`sessionByLane_`, a recycled lane name resuming a dead session),
    and the reason `_TEMPLATE.md` §2 now asks this question of every record that names a lane.
    """
    env.build()
    lane = env.claim("TCK-001")
    first = env.start(lane)
    env.wait_log(lane, "ready")
    env.release(lane)
    env.wait_lanes(0)

    again = env.claim("TCK-001")
    assert again == lane, f"the lane name was not recycled, so this case measures nothing: {again}"
    second = env.start(again)
    assert second["run_id"] != first["run_id"], (first, second)

    # TODAY: the run number continues from the previous occupant rather than restarting. When
    # SRV-FIX-247's class is fixed, this assertion is the one to flip, deliberately.
    n_first = int(first["run_id"].split("-")[1])
    n_second = int(second["run_id"].split("-")[1])
    assert n_second == n_first + 1, (
        f"the run counter no longer carries across a released lane name ({n_first} -> {n_second}); "
        f"if that was fixed on purpose, flip this assertion and say which ticket did it")


def test_the_session_record_still_names_a_released_lane(env):
    """FIXED by `SRV-FIX-532` cluster C3 — the record dies with the lane it names.

    The third record §3 names. It was PINNED here as it behaved then: a lane name is recycled —
    release `lane/01`, claim it again — and a `resume` start found the PREVIOUS occupant's session
    id still filed under that name, which is `SRV-FIX-247`'s defect, a new occupant resuming a dead
    session. The pin existed so that fixing it would be a deliberate act with this case in the diff.

    `SRV-FIX-532` §1 C3 is that act: *"a re-claimed lane never resumes the released lane's
    session"*. `agent::Session::forgetLane()` erases `sessionByLane_` and `runCountByLane_`, and
    `api::Manager` calls it on `onMembership(claimed=false)`. So the assertion is flipped to the
    behaviour that ticket asked for, and the case now pins THAT — a regression to the old record
    lands back here.

    Evidence for the criterion itself is
    `test_lane_recovery.py::test_a_reclaimed_lane_does_not_resume_the_old_session`, which drives
    the whole shape; this case is the record's own pin and stays where the audit heading can see it.
    """
    env.build()
    lane = env.claim("TCK-001")
    first = env.start(lane)
    env.wait_log(lane, "ready")
    first_session = env.row(lane)["session_id"]
    assert first_session, env.row(lane)
    env.release(lane)
    env.wait_lanes(0)

    again = env.claim("TCK-001")
    assert again == lane, f"the lane name was not recycled, so this case measures nothing: {again}"
    r = env.srv.post(f"/api/lanes/{again}/runs", json={"resume": True})
    assert r.status_code == 201, r.text
    resumed = env.row(again)["session_id"]

    # SRV-FIX-532 cluster C3: the record died with the lane, so the new occupant starts its own.
    assert resumed and resumed != first_session, (
        f"a resume in a recycled lane reached the previous occupant's session again "
        f"({first_session} -> {resumed}). SRV-FIX-532 cluster C3 made the record die with the lane "
        f"it names — agent::Session::forgetLane(), called from api::Manager on "
        f"onMembership(claimed=false) — and this is SRV-FIX-247's defect returning: the new "
        f"occupant is handed a conversation that is gone, the run dies, and the lane reports "
        f"`crashed` with no reason")


def test_every_record_the_audit_heading_names_has_a_case_under_it():
    """MANDATORY — the heading is answerable to its own contents.

    Round 2 of this ticket wrote a heading naming three records and two functions beneath it, and
    nothing compared the lists: the comment said `sessionByLane_` was "Pinned below" and
    `ESCAPES.md` said all three were "audited under one heading", while no case named it. That is
    this ticket's own subject — a record claiming something that is not true — committed into the
    fix for it.

    Parsed from the file rather than maintained by hand, so a fourth record added to the heading
    cannot be forgotten the way the third was.
    """
    import re as _re
    from pathlib import Path as _Path

    text = _Path(__file__).read_text()
    start = text.index("# ── SRV-FIX-256 §3 · the audit")
    heading = text[start:text.index("\ndef ", start)]
    named = _re.findall(r"^#\s+(\w+_)\s+—", heading, _re.M)
    assert len(named) >= 3, f"the audit heading names {named}; §3 enumerates three records"

    body = text[start:]
    missing = [n for n in named if n not in body.split(heading, 1)[-1]]
    assert not missing, (
        f"the audit heading names {missing} but no case under it mentions them. A heading that "
        f"lists a record with no case is the defect this ticket exists to close, one level up.")


# ── SRV-FIX-276 · the trigger lets go when its turn ends ──────────────────────────────────────
#
# `SRV-FIX-123` made a role lane DEFER rather than bypass the queue, on a stated assumption: "A
# held resource is temporary by construction — the holder releases it." It is not, when the holder
# is the lane that spawned the waiter. Measured three times on 2026-08-27 — `FIX-083` twice and
# `WEB-FEAT-008` — a run lane `idle` at its QA stage kept `local-ports` while the `qa` lane the
# tool had started for the same ticket sat `queued` behind it, indefinitely, until a person ran
# `E16` and then `E13`.
#
# The distinction neither ticket drew is the trigger's PROCESS, not its stage report. `123`'s case
# has the trigger's agent alive and must keep deferring — two agents on one ESP32 produce one
# corrupted run. Today's cases had the trigger's turn over. So the resources move when the trigger
# stops working, and the pair below is what holds both statements true at once.

TURN_END = '{"type":"result","subtype":"success","duration_ms":10,"total_cost_usd":0.01,' \
           '"is_error":false,"result":"done"}\n'


def test_a_role_lane_does_not_wait_for_the_lane_that_started_it_once_that_lane_is_idle(env):
    """THE assertion. Nobody releases the run lane, and the checker still runs."""
    env.build(resources=RESOURCE)
    lane = env.claim("TCK-001")
    assert env.row(lane)["resources_held"] == ["board-master"], env.row(lane)
    env.start(lane)
    env.wait_log(lane, "ready")
    Path(real(env.row(lane)["worktree"]), "turn.jsonl").write_text(TURN_END)

    env.report(lane, "GATE")
    role = role_lane(env, lane)

    env.say(lane, "emit:turn.jsonl")          # the trigger's turn ends; nothing is released
    env.wait_log(lane, "emitted=turn.jsonl")

    env.wait_log(role, "ready", timeout=30)
    assert env.row(role)["resources_held"] == ["board-master"], env.row(role)
    assert env.row(lane)["resources_held"] == [], (
        "the trigger still holds what its own checker needs")


def test_a_role_lane_still_waits_while_the_lane_that_started_it_is_running(env):
    """MANDATORY NEGATIVE — `SRV-FIX-123`'s case, re-asserted so this ticket cannot take it back.

    The trigger's agent is alive and has ended no turn. Transferring here would put two agents on
    one board, which is the corrupted run `board-master` exists to prevent. An implementation that
    hands over on the stage report alone goes red here.
    """
    env.build(resources=RESOURCE)
    lane = env.claim("TCK-001")
    env.start(lane)
    env.wait_log(lane, "ready")

    env.report(lane, "GATE")
    role = role_lane(env, lane)

    env.settle()
    log = env.log(role)
    assert "argv=" not in log, log
    assert "ready" not in log, log
    assert env.row(lane)["resources_held"] == ["board-master"], "the working holder let go"


def test_a_role_deferred_for_a_seat_still_gets_the_resource_when_it_claims(env):
    """ROUND 2, transcribed from SRV-FIX-276's QA, F1 — the handover had no third site.

    Round 1 handed the resources over from two places: role admission, and the trigger's turn
    ending while a role lane was already queued. Neither fires when the role is deferred for a
    **seat**: at admission there is no seat, so it goes to `pendingRoles_` with no lane at all; and
    when the turn ends there is still no lane, so nothing is queued to notice. The seat frees
    later, the lane is claimed, it queues on the resource, and the deadlock is exactly the one this
    ticket exists to end.

    QA's raw output, on the fix:

        trigger lane/01 TCK-001 role run      process idle   resources_held ['board-master']
        role    lane/03 TCK-001 role reviewer process queued resources_held []
                waiting_for {'resource': 'board-master', 'held_by': 'lane/01', ...}
    """
    env.build(resources=RESOURCE, lanes_host=2,
              workspaces={"core": {"path": "."}, "side": {"path": "."}})
    lane = env.claim("TCK-001")
    env.start(lane)
    env.wait_log(lane, "ready")
    Path(real(env.row(lane)["worktree"]), "turn.jsonl").write_text(TURN_END)

    filler = env.claim("TCK-002", workspace="side")      # the second and last seat
    env.report(lane, "GATE")                             # no seat: the role is pending, not queued
    env.say(lane, "emit:turn.jsonl")                     # the turn ends with no lane to notice
    env.wait_log(lane, "emitted=turn.jsonl")
    env.settle()

    env.release(filler)                                  # a seat frees; the role lane is claimed
    role = role_lane(env, lane, filler, timeout=30)

    env.wait_log(role, "ready", timeout=30)
    assert env.row(role)["resources_held"] == ["board-master"], env.row(role)
def _an_idle_lane_holding_the_board(env):
    """TCK-001 takes the exclusive resource, reaches `ready`, then ends its turn and sits idle."""
    env.build(resources=RESOURCE, lanes_host=3)
    lane = env.claim("TCK-001")
    env.start(lane)
    env.wait_log(lane, "ready")
    Path(real(env.row(lane)["worktree"]), "turn.jsonl").write_text(TURN_END)
    env.say(lane, "emit:turn.jsonl")
    env.wait_log(lane, "emitted=turn.jsonl")
    env.settle()
    assert env.row(lane)["resources_held"] == ["board-master"], env.row(lane)
    return lane


def test_a_role_lane_still_waits_for_a_DIFFERENT_ticket_s_lane(env):
    """§1 criterion 3 — **mandatory negative**.

    The handover is scoped to the ticket that spawned the waiter. An idle lane is still a lane doing
    a ticket's work: it holds `board-master` because a person may yet type into it, and the one
    thing that makes taking the board away from it safe is that the taker is the SAME ticket's own
    checker, started by the tool from that lane's own stage report.

    Drop the ticket match in `reclaimFromIdleTrigger_` and this goes red: TCK-002 walks off with the
    board of a ticket it has nothing to do with, which is `SRV-FIX-123`'s corrupted run with an
    extra step. Watched red against exactly that mutant.

    The other half of the scope — the refusal to serve a `run` lane — has **no case here, and this
    says so rather than implying one**. It cannot be reached through the API: a second run lane for
    one ticket is `409 lane_exists` ("this ticket already has a lane in that role"), so with the
    ticket match in place the only caller the refusal could ever turn away does not exist. It is
    defensive code, and a test that pretended to fail on it would be green either way.
    """
    holder = _an_idle_lane_holding_the_board(env)

    waiter = env.claim("TCK-002", role="reviewer")      # a role lane, but of a different ticket
    env.settle(4)

    assert env.row(waiter)["state"]["process"] == "queued", env.row(waiter)["state"]
    assert env.row(waiter)["resources_held"] == []
    assert env.row(holder)["resources_held"] == ["board-master"], (
        "an idle lane's board was taken by a lane of another ticket")


def test_the_wait_is_never_unbounded_and_says_who_holds_it(env):
    """§1 criterion 4 — a wait a person can read, and a wait that ends on its own.

    Two halves, because a queue that never answers *who* is the incident this ticket was filed from:
    three times an operator watched a lane sit at `queued` with nothing on the screen saying what it
    was behind, and each time the only way out was to guess and release a lane by hand.

    - **legible** — `E9` names the resource, the holder, whether that holder is alive, and the
      position in the queue. Asserted on a wait that genuinely does not end (criterion 3's).
    - **bounded** — the wait this ticket exists to end terminates with nobody touching a lane.
      Asserted by the resource arriving at the role lane in the criterion-1 fixture.
    """
    holder = _an_idle_lane_holding_the_board(env)
    waiter = env.claim("TCK-002", role="reviewer")
    env.settle(4)

    waiting = env.row(waiter)["waiting_for"]
    assert waiting["resource"] == "board-master", waiting
    assert waiting["held_by"] == holder, waiting
    assert waiting["holder_alive"] is True, waiting
    assert waiting["position"] == 1, waiting

    r = env.srv.get("/api/lanes")                       # E9 carries the resources beside the lanes
    assert r.status_code == 200, r.text
    res = {x["name"]: x for x in r.json()["resources"]}["board-master"]
    assert res["held_by"] == holder and res["queue"] == [waiter], res


def _board(env):
    return {x["name"]: x for x in env.srv.get("/api/lanes").json()["resources"]}["board-master"]


def test_the_board_is_never_orphaned_when_a_stranger_is_already_queued(env):
    """ROUND 3, transcribed from SRV-FIX-276's QA round 2, F1 — a deadlock ROUND 2 CREATED.

    Round 2 reclaimed inside `grant_`, which clears the holder and then tests
    `allFree = holder_[n].empty() && queue_[n].empty()`. With a third ticket already queued for the
    board that is false, so the role lane is pushed to the BACK of the queue — and `grant_` never
    calls `advance_`, nor does `reserve_` on that path. The board ends up held by nobody with two
    lanes waiting for it, which is strictly worse than the bug the ticket was filed for.

    QA's raw output, identical at every 6 s sample for 30 s:

        t+ 30s  board held_by=None alive=None queue=['lane/02', 'lane/04']
                trigger=idle/[]  role=queued/[]

    The assertion is on the CONSEQUENCE — that the board has an owner — and not on which lane owns
    it: with the queue in the order it is, `advance_` grants the stranger first and the role lane
    second, and both are correct answers to this fixture.
    """
    env.build(resources=RESOURCE, lanes_host=3,
              tickets=("TCK-001", "TCK-002", "TCK-003"),
              workspaces={"core": {"path": "."}, "side": {"path": "."}})
    lane = env.claim("TCK-001")
    env.start(lane)
    env.wait_log(lane, "ready")
    Path(real(env.row(lane)["worktree"]), "turn.jsonl").write_text(TURN_END)

    stranger = env.claim("TCK-003")                 # already queued before the role is requested
    filler = env.claim("TCK-002", workspace="side")  # the last seat, so the role defers for one
    env.settle()

    env.report(lane, "GATE")
    env.say(lane, "emit:turn.jsonl")
    env.wait_log(lane, "emitted=turn.jsonl")
    env.settle()

    env.release(filler)
    role = role_lane(env, lane, stranger, timeout=30)
    env.settle(6)

    res = _board(env)
    assert res["held_by"], (
        f"board-master orphaned while {res['queue']} wait for it: {res}; "
        f"stranger={env.row(stranger)['state']['process']}, "
        f"role={env.row(role)['state']['process']}")


def test_a_finished_trigger_hands_over_without_anyone_releasing_it(env):
    """ROUND 3, transcribed from SRV-FIX-276's QA round 2, F2 — §1's other listed member.

    §1 decides the transfer fires when the trigger's process is not `running` — **`idle` or
    `finished`**. Rounds 1 and 2 delivered `idle` only. A session that ends without a final turn
    report never reaches `setTurn`, and `E16` is exactly that: the operator ends the session, the
    lane's `pid` becomes 0, `holder_alive` goes false on the screen — and nothing acts on it.

    QA's raw output, unchanged over 30 s:

        t+ 30s  board held_by='lane/01' alive=False queue=['lane/02']
                trigger=finished/['board-master']  role=queued/[]

    This is §1's own incident with one step removed: the operator ran `E16` and then still had to
    run `E13`. A crashed agent reaches the same state, so this case is the reaper's too.
    """
    env.build(resources=RESOURCE, lanes_host=3)
    lane = env.claim("TCK-001")
    env.start(lane)
    env.wait_log(lane, "ready")

    env.report(lane, "GATE")                        # the turn never ends — no turn.jsonl, no emit
    role = role_lane(env, lane, timeout=30)

    r = env.srv.post(f"/api/lanes/{lane}/end", json={})
    assert r.status_code in (200, 202), r.text

    env.settle(8)
    assert env.row(role)["resources_held"] == ["board-master"], (
        f"a finished trigger kept the board: {_board(env)}; role={env.row(role)}")
