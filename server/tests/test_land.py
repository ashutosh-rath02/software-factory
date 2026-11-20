"""SRV-FEAT-437 — a PASS verdict lands its branch.

`docs/technical/MODEL.md` §4 D3: when a verdict file that says PASS lands on a ticket's branch, the tool
squash-merges that branch to `main` with the author derived from the branch's non-merge commits,
pushes, and reaps the branch and the worktree. It does it on its own account, never a lane's.

Every case builds its own repository with its own bare remote and points a server at it. Nothing
here touches this repository or any real remote — §1's safety clause, and the reason a suite that
merges is safe to run at all.
"""

import json
import os
import subprocess
import time

import pytest

from test_agent_profiles import PEER, CAPTURE


REQ_DIR = "docs/epics"
TKT_DIR = "docs/tickets"
RPT_DIR = "docs/reports"

MEMBER = ("Member One", "member@example.invalid")
MERGER = ("Merger Two", "merger@example.invalid")
REVIEWER = ("Reviewer Three", "reviewer@example.invalid")

# What a landing may never carry. The enumeration it is checked against is swept off the
# product's own log, so this is the rule, never a list of what the tool was seen to run.
FORCE_TOKENS = ("--force", "-f", "--force-with-lease")


def a_scratch_removal(argv):
    """`git worktree remove --force <a scratch this tool made>` — the one forced invocation.

    `SRV-FIX-518`. A scratch worktree is the tool's own, created by `openScratch_` and removed by
    `closeScratch_` a few lines later, and a merge left in conflict is a checkout git will not
    remove unforced. Refusing that one removal is what left the scratch behind and turned every
    later attempt on the same tip into `no scratch checkout could be made`.

    Every other removal keeps its refusal: a lane's checkout holds somebody's work, a push moves a
    shared ref, a branch delete destroys commits. So the exception is recognised by its SHAPE —
    the subcommand, the flag's position, and the `factory-land-` naming `openScratch_` alone
    produces — and the sweep below asserts both halves: at least one removal of this shape, and no
    forced invocation of any other.
    """
    if "worktree" not in argv:
        return False
    at = argv.index("worktree")
    return (argv[at + 1:at + 3] == ["remove", "--force"]
            and len(argv) == at + 4
            and os.path.basename(argv[at + 3]).startswith("factory-land-"))

IGNORES = ".factory/lanes/\n.factory/runs/\n.factory/worktrees/\n"


def manifest_for():
    return {
        "factory": "0.1",
        "workspaces": {"alpha": {"path": "."}},
        "docs": {"epics": REQ_DIR, "tickets": TKT_DIR, "reports": RPT_DIR},
        "status": {"in_flight": ["in-progress", "awaiting-qa"]},
        "agent": {"command": "claude"},
    }


def doc(front, body="Body."):
    lines = ["---"]
    for k, v in front.items():
        lines.append(f"{k}: {v}")
    lines += ["---", "", body, ""]
    return "\n".join(lines)


class G:
    """git, run where the test says, failing loudly."""

    def __init__(self, cwd):
        self.cwd = str(cwd)

    def __call__(self, *args, cwd=None, when=None):
        env = None
        if when is not None:
            env = dict(os.environ, GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
        out = subprocess.run(["git", "-C", str(cwd or self.cwd), *args],
                             capture_output=True, text=True, timeout=60, env=env)
        assert out.returncode == 0, (
            f"git {' '.join(args)} in {cwd or self.cwd} → {out.returncode}\n"
            f"{out.stdout}\n{out.stderr}")
        return out.stdout.strip()

    def ok(self, *args, cwd=None):
        return subprocess.run(["git", "-C", str(cwd or self.cwd), *args],
                              capture_output=True, text=True, timeout=60).returncode == 0

    def commit(self, message, who=MEMBER, cwd=None, when=None):
        self("add", "-A", cwd=cwd)
        self("-c", f"user.name={who[0]}", "-c", f"user.email={who[1]}",
             "commit", "--no-gpg-sign", "-m", message, cwd=cwd, when=when)
        return self("rev-parse", "HEAD", cwd=cwd)


class World:
    """A repository, a bare remote for it, and somewhere outside it to put worktrees."""

    def __init__(self, repo, origin, wt_root):
        self.repo = repo
        self.root = repo.root
        self.origin = origin
        self.wt_root = wt_root
        self.git = G(repo.root)

    def write(self, rel, text, cwd=None):
        p = (cwd or self.root) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def worktree(self, branch, base="main"):
        """Where the TOOL puts a lane's checkout — `<root>/.factory/worktrees/…`.

        Provenance, not cleanliness, is what licenses the tool to remove a checkout (S7's first
        sentence), and the tool's own worktrees directory is the durable form of it: a lane record
        is gone the moment the lane is released. A fixture that put lane checkouts anywhere else
        was testing a shape the product never creates.
        """
        path = self.root / ".factory" / "worktrees" / branch.replace("/", "_")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.git("worktree", "add", "-b", branch, str(path), base)
        return path

    def push_main(self):
        self.git("push", "origin", "main")

    def tip(self, ref, cwd=None):
        return self.git("rev-parse", ref, cwd=cwd)

    def tree(self, ref, cwd=None):
        return self.git("rev-parse", f"{ref}^{{tree}}", cwd=cwd)

    def has_branch(self, name):
        return self.git.ok("rev-parse", "--verify", "--quiet", f"refs/heads/{name}")

    def remote_main(self):
        return self.git("rev-parse", "main", cwd=self.origin)


@pytest.fixture
def world(repo, tmp_path_factory):
    repo.manifest(manifest_for())
    repo.write(f"{REQ_DIR}/REQ-001-landing.md",
               doc({"id": "REQ-001", "title": "Work reaches main", "status": "draft"}))
    repo.write(f"{RPT_DIR}/.keep", "")
    repo.write("src/base.txt", "the repository as it stands\n")
    # A server pointed at this repository makes `.factory/lanes` and friends; untracked content at
    # the root would refuse every landing for a reason that has nothing to do with the case.
    repo.write(".gitignore", IGNORES)

    g = G(repo.root)
    g("init", "-b", "main")
    g("config", "commit.gpgsign", "false")
    g.commit("the repository as it stands", who=MEMBER)

    origin = tmp_path_factory.mktemp("origin") / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)],
                   capture_output=True, text=True, timeout=30, check=True)
    g("remote", "add", "origin", str(origin))
    g("push", "-u", "origin", "main")

    return World(repo, origin, tmp_path_factory.mktemp("worktrees"))


def ticket_on_main(w, tid, title="A thing to land", status="awaiting-qa"):
    w.write(f"{TKT_DIR}/{tid}.md",
            doc({"id": tid, "parent": "REQ-001", "workspace": "alpha",
                 "title": title, "status": status}))
    w.git.commit(f"file {tid}", who=MEMBER)


def set_status(w, tid, status, cwd):
    p = cwd / TKT_DIR / f"{tid}.md"
    text = p.read_text()
    assert "status: awaiting-qa" in text, f"{tid}.md does not say awaiting-qa:\n{text}"
    p.write_text(text.replace("status: awaiting-qa", f"status: {status}"))


def work_branch(w, tid, *, extra_authors=False):
    """What a run lane leaves behind: the work and the report, committed by the member."""
    wt = w.worktree(f"ticket/{tid}")
    w.write(f"src/{tid}.txt", f"{tid} did something\n", cwd=wt)
    w.git.commit(f"{tid}: the work", who=MEMBER, cwd=wt)
    w.write(f"{RPT_DIR}/{tid}-report.md", f"# {tid} — report\n\nIt was built.\n", cwd=wt)
    w.git.commit(f"{tid}: the report", who=MEMBER, cwd=wt)
    if extra_authors:
        w.write(f"src/{tid}-more.txt", "a second pair of hands\n", cwd=wt)
        w.git.commit(f"{tid}: a second pair of hands", who=MERGER, cwd=wt)
    return wt


def verdict_text(tid, verdict="PASS WITH MINORS"):
    return (f"# {tid} — QA verdict — {verdict}\n"
            f"QA session: separate from the implementing session\n")


def plant_role_run(w, tid, role, *, run_id=None, lane="lane/07", branch=None, tip=None):
    """One run record, as `agent::history::write` records one, for a session the TOOL started.

    Modelled on `test_runs_index.plant_run`, which carries the same note. `.factory/runs/` is the
    tool's own ledger of which sessions it dispatched, for which ticket, in which role. It is
    written from the control plane and it sits outside every lane's writable root, so a lane
    cannot forge one — which is the whole reason the separate-session guard rests on it rather
    than on a ref. `read_back_by_the_product` drives the product's own parser over these bytes.
    """
    run_id = run_id or f"run-{tid}-{role}"
    d = w.root / ".factory" / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    if branch is None:
        branch = f"ticket/{tid}-{role}"
    if tip is None:
        # What the tool records: the tip that branch was at when the session ended. A branch the
        # session never made leaves an empty tip, which is what a record from a session that
        # committed nothing looks like.
        tip = w.git("rev-parse", f"refs/heads/{branch}") if w.has_branch(branch) else ""
    meta = {"run_id": run_id, "lane": lane, "ticket_id": tid, "role": role,
            "branch": branch, "branch_tip": tip,
            "session_id": f"sess-{run_id}", "n": 1,
            "started_iso": "2026-09-07T10:00:00Z", "ended_iso": "2026-09-07T10:20:00Z",
            "outcome": "completed", "duration_s": 1200, "cost_usd": 1.25, "turns": 4,
            "exit_code": 0, "signal": None, "unmapped": {}}
    (d / "meta.json").write_text(json.dumps(meta))
    (d / "transcript.jsonl").write_text(
        json.dumps({"seq": 1, "ts": "2026-09-07T10:00:00Z", "kind": "text", "text": "qa"}) + "\n")
    return run_id


def read_back_by_the_product(srv, run_id):
    """The planted record, read by the product's own parser rather than by the test's."""
    r = srv.get("/api/runs")
    assert r.status_code == 200, f"/api/runs → {r.status_code} {r.text}"
    rows = {row["run_id"]: row for row in r.json().get("runs", [])}
    assert run_id in rows, (
        f"the product does not read the record this fixture planted, so the fixture is a "
        f"stand-in for nothing: {sorted(rows)}")
    return rows[run_id]


def qa_branch(w, tid, wt, *, verdict="PASS WITH MINORS", merge_by=MERGER):
    """What a QA role lane leaves: its own branch `ticket/<id>-qa` carrying the verdict.

    `lane::Store::branchFor_` names that branch and `E13` keeps it, so it is the ref that makes
    the verdict somebody else's — the evidence §1 criterion 4(c) says has to exist.
    """
    qa_wt = w.worktree(f"ticket/{tid}-qa", base=f"ticket/{tid}")
    w.write(f"{RPT_DIR}/{tid}-qa.md", verdict_text(tid, verdict), cwd=qa_wt)
    set_status(w, tid, "pass", qa_wt)
    w.git.commit(f"{tid}: QA verdict — {verdict}", who=REVIEWER, cwd=qa_wt)
    w.git("worktree", "remove", str(qa_wt))
    w.git("-c", f"user.name={merge_by[0]}", "-c", f"user.email={merge_by[1]}",
          "merge", "--no-ff", "--no-edit", "--no-gpg-sign", f"ticket/{tid}-qa", cwd=wt)
    plant_role_run(w, tid, "qa")


def self_passed(w, tid, wt, *, verdict="PASS WITH MINORS"):
    """The implementer passing itself: the verdict is on the run branch and on no other ref."""
    w.write(f"{RPT_DIR}/{tid}-qa.md", verdict_text(tid, verdict), cwd=wt)
    set_status(w, tid, "pass", wt)
    w.git.commit(f"{tid}: QA verdict — {verdict}", who=MEMBER, cwd=wt)


def serve_fast(serve, root, **env):
    return serve(root, FACTORY_DELIVERY_POLL_MS="200", **env)


def wait_until(what, predicate, timeout=60):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(0.15)
    raise AssertionError(f"{what} never happened within {timeout}s; the last look was {last!r}")


def settle(seconds=5):
    """Long enough that anything else the tool meant to do would have happened."""
    time.sleep(seconds)


def delivery_of(srv, tid):
    r = srv.get(f"/api/tickets/{tid}")
    assert r.status_code == 200, f"{tid} → {r.status_code} {r.text}"
    return r.json()["delivery"]


def spawned(srv):
    """Every argv the product spawned, read off its own log — not a list anybody typed."""
    out = []
    for line in srv.log.read_text().splitlines():
        at = line.find("land: ran ")
        if at < 0:
            continue
        out.append(json.loads(line[at + len("land: ran "):]))
    return out


def land_lines(srv):
    return [line[line.index("land: "):] for line in srv.log.read_text().splitlines()
            if "land: " in line and "land: ran " not in line]


VERDICT_PEER = PEER.replace(
    "            elif text != 'hold':\n                complete(phase)",
    """            elif text != 'hold':
                if text.startswith('verdict:'):
                    _, tid, verdict = text.split(':')
                    ticket = 'docs/tickets/' + tid + '.md'
                    report = 'docs/reports/' + tid + '-qa.md'
                    with open(ticket) as source:
                        current = source.read()
                    with open(ticket, 'w') as target:
                        target.write(current.replace('status: awaiting-qa',
                                                     'status: pass' if verdict == 'PASS' else 'status: fail'))
                    with open(report, 'w') as target:
                        target.write('# ' + tid + ' — QA verdict — ' + verdict + '\\n')
                elif text.startswith('move:'):
                    _, tid, number = text.split(':')
                    with open('src/' + tid + '-turn.txt', 'w') as target:
                        target.write(number + '\\n')
                if text.startswith(('verdict:', 'move:')):
                    subprocess.run(['git', 'add', '-A'], check=True)
                    subprocess.run(['git', '-c', 'user.name=Reviewer Three',
                                    '-c', 'user.email=reviewer@example.invalid',
                                    'commit', '--no-gpg-sign', '-m', 'role turn'], check=True,
                                   stdout=subprocess.DEVNULL)
                complete(phase)"""
)
assert VERDICT_PEER != PEER


def live_role_world(w, tmp_path_factory, *, tid, work=True):
    credentials = tmp_path_factory.mktemp('codex-credentials')
    (credentials / 'auth.json').write_text('{"token":"fixture-secret"}\n')
    w.write('agent.py', VERDICT_PEER)
    w.write(CAPTURE.name, CAPTURE.read_text())
    manifest = json.loads((w.root / '.factory/factory.json').read_text())
    manifest.pop('agent', None)
    manifest['default_agent'] = 'codex'
    manifest['agents'] = {
        'codex': {'driver': 'codex', 'command': '/usr/bin/python3 agent.py',
                  'models': ['fixture'], 'default_model': 'fixture',
                  'credentials_dir': str(credentials)},
        'claude': {'driver': 'claude', 'command': '/usr/bin/python3 claude.py',
                   'models': ['fixture'], 'default_model': 'fixture'},
    }
    (w.root / '.factory/factory.json').write_text(json.dumps(manifest))
    w.write('claude.py', '''import json, subprocess, sys
for line in sys.stdin:
    if 'message' not in line:
        continue
    with open('src/claude-turn.txt', 'w') as target:
        target.write('completed\\n')
    subprocess.run(['git', 'add', '-A'], check=True)
    subprocess.run(['git', '-c', 'user.name=Reviewer Three',
                    '-c', 'user.email=reviewer@example.invalid',
                    'commit', '--no-gpg-sign', '-m', 'role turn'], check=True,
                   stdout=subprocess.DEVNULL)
    print(json.dumps({'type': 'result', 'result': 'done'}), flush=True)
    break
''')
    w.git.commit('recorded agent peers', who=MEMBER)
    ticket_on_main(w, tid)
    w.push_main()
    if work:
        work_branch(w, tid)


def start_role(srv, tid, role='qa', agent='codex', prompt=''):
    claimed = srv.post('/api/lanes', json={'ticket_id': tid, 'workspace': 'alpha', 'role': role})
    assert claimed.status_code == 201, claimed.text
    lane = claimed.json()
    started = srv.post(f"/api/lanes/{lane['lane']}/runs",
                       json={'agent': agent, 'prompt': prompt})
    assert started.status_code == 201, started.text
    return lane, started.json()


def run_meta(w, run_id):
    path = w.root / '.factory' / 'runs' / run_id / 'meta.json'
    return json.loads(path.read_text()) if path.exists() else {}


def test_an_idle_codex_qa_session_lands_its_verdict(world, serve, tmp_path_factory):
    """SRV-FIX-586 under SRV-FIX-600: the verdict lands while the app-server idles, then landing
    ends that idle session and releases its lane.

    The order is read off the product's own log, not off a transient `running` receipt: the land
    line comes first, then delivery naming this lane's idle session as ended for landed work, then
    the run's end. A lander that waits for the session to exit never lands (the session is ended
    only after landing), so it goes red here.
    """
    w = world
    tid = 'T-IDLE'
    live_role_world(w, tmp_path_factory, tid=tid)
    before = w.tip('main')
    srv = serve(w.root, FACTORY_DELIVERY_POLL_MS='120000')
    lane, started = start_role(srv, tid, prompt=f'verdict:{tid}:PASS')
    run_id = started['run_id']
    assert started['pid'] > 0
    try:
        wait_until('the committed verdict to land while the role idles',
                   lambda: w.tip('main') != before, timeout=60)
    except AssertionError as error:
        said = [line for line in land_lines(srv) if tid in line]
        server_lines = srv.log.read_text().splitlines()
        threads = {}
        for task in Path(f'/proc/{srv.proc.pid}/task').glob('*/wchan'):
            try:
                threads[task.parent.name] = task.read_text().strip()
            except OSError:
                pass
        ticket = srv.get(f'/api/tickets/{tid}')
        raise AssertionError(
            f'{error}; meta={run_meta(w, run_id)}; role_tip={w.tip(f"ticket/{tid}-qa")}; '
            f'lander={said[-12:]}; process={srv.proc.poll()}; '
            f'ticket={ticket.status_code} {ticket.text[:1200]}; '
            f'threads={threads}; server_lines={server_lines[-200:]}') from error
    ended = wait_until('the landed role session to end',
                       lambda: (m if (m := run_meta(w, run_id)).get('outcome') not in
                                (None, 'running') else None), timeout=30)
    wait_until('the landed role lane to be released',
               lambda: all(row['lane'] != lane['lane']
                           for row in srv.get('/api/lanes').json()['lanes']), timeout=30)
    assert ended['outcome'] == 'ended cleanly', ended
    assert ended['branch_tip'] == w.tip(f'ticket/{tid}-qa'), ended
    assert srv.proc.poll() is None
    assert w.remote_main() == w.tip('main')
    assert 'status: pass' in (w.root / TKT_DIR / f'{tid}.md').read_text()

    log = srv.log.read_text().splitlines()
    def first(what, *words):
        at = [i for i, line in enumerate(log) if all(word in line for word in words)]
        assert len(at) == 1, f'{what} should be logged exactly once: {[log[i] for i in at]}'
        return at[0]
    landed = first('the landing', f'land: {tid} landed ticket/{tid} on main')
    ending = first('the idle session ended for landed work', f"lane {lane['lane']} holds {tid}",
                   'finished its turn', 'ending its session')
    exited = first('the run end', f'run {run_id} in {lane["lane"]} ended')
    assert landed < ending < exited, (
        f'the verdict must land while the session idles and only then end it: '
        f'{[log[landed], log[ending], log[exited]]}')


def test_each_completed_turn_restamps_the_role_tip(world, serve, tmp_path_factory):
    w = world
    tid = 'T-TURNS'
    live_role_world(w, tmp_path_factory, tid=tid)
    ticket_on_main(w, 'T-CLAUDE')
    w.push_main()
    work_branch(w, 'T-CLAUDE')
    srv = serve(w.root, FACTORY_DELIVERY_POLL_MS='30000')
    lane, started = start_role(srv, tid, prompt=f'move:{tid}:one')
    first = wait_until('first completed turn tip',
                       lambda: run_meta(w, started['run_id']).get('branch_tip'), timeout=15)
    assert first == w.tip(f'ticket/{tid}-qa')
    sent = srv.post(f"/api/lanes/{lane['lane']}/messages", json={'text': f'move:{tid}:two'})
    assert sent.status_code in (200, 202), sent.text
    second = wait_until('second completed turn tip',
                        lambda: (tip if (tip := run_meta(w, started['run_id']).get('branch_tip'))
                                 != first else None), timeout=15)
    assert second == w.tip(f'ticket/{tid}-qa')
    assert run_meta(w, started['run_id'])['outcome'] == 'running'

    _, claude = start_role(srv, 'T-CLAUDE', agent='claude', prompt='commit and exit')
    ended = wait_until('Claude role exit tip',
                       lambda: (m if (m := run_meta(w, claude['run_id'])).get('branch_tip')
                                and m.get('outcome') != 'running' else None), timeout=15)
    assert ended['branch_tip'] == w.tip('ticket/T-CLAUDE-qa')


def test_turn_tips_never_land_a_run_role_or_a_fail(world, serve, tmp_path_factory):
    w = world
    tid = 'T-RUN'
    live_role_world(w, tmp_path_factory, tid=tid, work=False)
    ticket_on_main(w, 'T-FAIL')
    w.push_main()
    work_branch(w, 'T-FAIL')
    before = w.tip('main')
    srv = serve(w.root, FACTORY_DELIVERY_POLL_MS='200')
    lane, started = start_role(srv, tid, role='run', prompt=f'verdict:{tid}:PASS')
    wait_until('run role verdict commit',
               lambda: w.has_branch(f'ticket/{tid}')
               and w.git.ok('cat-file', '-e', f'ticket/{tid}:{RPT_DIR}/{tid}-qa.md'),
               timeout=15)
    settle(2)
    assert not run_meta(w, started['run_id']).get('branch_tip')
    assert w.tip('main') == before
    wait_until('run role refusal',
               lambda: any(tid in line and 'no role session recorded' in line
                           for line in land_lines(srv)), timeout=15)

    _, failed = start_role(srv, 'T-FAIL', prompt='verdict:T-FAIL:FAIL')
    fail_tip = wait_until('FAIL role receipt',
                          lambda: run_meta(w, failed['run_id']).get('branch_tip'), timeout=15)
    assert fail_tip == w.tip('ticket/T-FAIL-qa')
    settle(2)
    assert w.tip('main') == before
    assert w.git.ok('cat-file', '-e', 'ticket/T-FAIL-qa:docs/reports/T-FAIL-qa.md')
    wait_until('FAIL verdict refusal',
               lambda: any('T-FAIL' in line and 'does not say PASS' in line
                           for line in land_lines(srv)), timeout=15)


# ── criterion 1 ───────────────────────────────────────────────────────────────────────────────

def test_a_passed_branch_reaches_main_as_one_commit_and_is_reaped(world, serve):
    """A ticket a separate session passed lands, and the operator finds it on `main`.

    §1 criterion 1. `main` gains exactly one commit, its tree is the branch's tree byte for byte,
    its subject begins `T-1: `, the remote has it, the branch and its worktree are gone, and E5
    then says `done` with nothing needing a person.
    """
    w = world
    ticket_on_main(w, "T-1")
    wt = work_branch(w, "T-1")
    qa_branch(w, "T-1", wt)
    # The shape this repository actually has. `ticket/SRV-FIX-379-qa` holds round 2's FAIL and
    # `ticket/SRV-FIX-379` holds the later PASS WITH MINORS — 18304 bytes against 21710 — because
    # a verdict document moves between rounds and the run branch carries the current one. A guard
    # that asked whether the same BLOB was on a second ref would call that a self-pass and refuse
    # a real ticket forever, so the fixture makes the two copies differ before anything is landed.
    w.write(f"{RPT_DIR}/T-1-qa.md",
            verdict_text("T-1") + "Round 2: re-run after the fix, and it stands.\n", cwd=wt)
    w.git.commit("T-1: QA verdict — round 2", who=REVIEWER, cwd=wt)
    assert (w.git("rev-parse", "ticket/T-1:docs/reports/T-1-qa.md")
            != w.git("rev-parse", "ticket/T-1-qa:docs/reports/T-1-qa.md")), (
        "the two refs must hold DIFFERENT copies of the verdict, or this case cannot tell a "
        "guard that reads the document from one that reads its bytes")
    w.push_main()

    before = w.tip("main")
    branch_tree = w.tree("ticket/T-1")
    branch_tip = w.tip("ticket/T-1")

    srv = serve_fast(serve, w.root)
    wait_until("main to gain the landing", lambda: w.tip("main") != before)

    assert w.git("rev-list", "--count", f"{before}..main") == "1", (
        "main must gain exactly one commit:\n" + w.git("log", "--oneline", f"{before}..main"))
    assert w.tip("main^") == before, "the squash commit's parent is the main that was there"
    assert w.tree("main") == branch_tree, (
        "the landed tree must be the branch's tree byte for byte — the tool moves a ref over "
        "commits that already exist and authors no document of its own")
    subject = w.git("log", "-1", "--format=%s", "main")
    assert subject.startswith("T-1: "), f"the subject must carry the ticket id: {subject!r}"

    wait_until("the remote to have it", lambda: w.remote_main() == w.tip("main"))
    wait_until("the branch to be reaped", lambda: not w.has_branch("ticket/T-1"))
    wait_until("the worktree to be reaped", lambda: not os.path.exists(str(wt)))

    assert not w.git.ok("merge-base", "--is-ancestor", branch_tip, "main"), (
        "a squash makes none of the branch's commits an ancestor of main, so the reap can only "
        "be decided on the TREE — this is the fixture that says so")

    d = wait_until("E5 to say done",
                   lambda: (lambda x: x if x["state"] == "done" else None)(
                       delivery_of(srv, "T-1")))
    assert d["state"] == "done", d
    assert d["needs_you"] is False, d


# ── criterion 2 ───────────────────────────────────────────────────────────────────────────────

def test_the_squash_author_is_the_branch_s_own_and_merges_do_not_outvote_it(world, serve):
    """The person who wrote the branch is still its author afterwards.

    §1 criterion 2 · `docs/operations/ESCAPES.md` 2026-09-04 (`SRV-FIX-376` → `SRV-FIX-378`) reaching the
    code that now performs the merge. The fixture carries TWO KINDS that disagree — the member's
    own commits and the merger's `git merge` bookkeeping — and the case asserts that the fixture
    disagrees before it asserts anything about the product.
    """
    w = world
    ticket_on_main(w, "T-2")
    wt = work_branch(w, "T-2", extra_authors=True)
    for n in (1, 2):
        w.write(f"src/other-{n}.txt", f"somebody else's ticket {n}\n")
        w.git.commit(f"OTHER-{n}: somebody else's work", who=MERGER)
        w.git("-c", f"user.name={MERGER[0]}", "-c", f"user.email={MERGER[1]}",
              "merge", "--no-ff", "--no-edit", "--no-gpg-sign", "main", cwd=wt)
    qa_branch(w, "T-2", wt, merge_by=MERGER)
    w.push_main()

    everyone = [x for x in w.git("log", "--format=%an", "main..ticket/T-2").splitlines() if x]
    non_merge = [x for x in
                 w.git("log", "--no-merges", "--format=%an", "main..ticket/T-2").splitlines() if x]

    def most(names):
        return max(sorted(set(names)), key=names.count)

    assert most(everyone) != most(non_merge), (
        f"the fixture must be one where the two readings DISAGREE, or this case passes whichever "
        f"one the product used: all={sorted(everyone)} non-merge={sorted(non_merge)}")
    assert most(non_merge) == MEMBER[0], (
        f"the member must win the reading that counts: {sorted(non_merge)}")

    before = w.tip("main")
    srv = serve_fast(serve, w.root)
    wait_until("main to gain the landing", lambda: w.tip("main") != before)

    author = w.git("log", "-1", "--format=%an <%ae>", "main")
    committer = w.git("log", "-1", "--format=%cn", "main")
    body = w.git("log", "-1", "--format=%B", "main")

    assert author == f"{MEMBER[0]} <{MEMBER[1]}>", (
        f"the branch's own author must survive the squash — the tool recorded {author!r}. "
        f"Counting merge commits names {most(everyone)!r}, and a merge commit is not authorship.")
    assert committer != MEMBER[0], (
        f"the tool lands on its own account, never a member's: the committer is {committer!r}")
    for other in sorted({a for a in non_merge if a != MEMBER[0]}):
        assert f"Co-Authored-By: {other}" in body, (
            f"every other author on the branch is a trailer; {other!r} is missing from:\n{body}")
    assert f"Co-Authored-By: {MEMBER[0]}" not in body, "the author is not also a trailer"


# ── criterion 3 ───────────────────────────────────────────────────────────────────────────────

def test_a_conflict_lands_nothing_and_names_the_paths(world, serve):
    """A `main` that moved is merged in first, and a conflict stops the landing.

    §1 criterion 3. Nothing lands, no remote ref moves, the branch's own tip is unchanged because
    the failed merge is aborted, and E5 says `needs_you` naming the conflicting path.
    """
    w = world
    ticket_on_main(w, "T-3")
    w.write("src/shared.txt", "one\ntwo\nthree\n")
    w.git.commit("the file both sides touch", who=MEMBER)
    w.push_main()

    wt = w.worktree("ticket/T-3")
    w.write("src/shared.txt", "one\nBRANCH\nthree\n", cwd=wt)
    w.git.commit("T-3: the branch's line", who=MEMBER, cwd=wt)
    w.write(f"{RPT_DIR}/T-3-report.md", "# T-3 — report\n", cwd=wt)
    w.git.commit("T-3: the report", who=MEMBER, cwd=wt)
    qa_branch(w, "T-3", wt)

    w.write("src/shared.txt", "one\nMAIN\nthree\n")
    w.git.commit("somebody else's line, on main", who=MERGER)
    w.push_main()

    main_before = w.tip("main")
    tree_before = w.tree("main")
    remote_before = w.remote_main()
    branch_before = w.tip("ticket/T-3")

    srv = serve_fast(serve, w.root)
    d = wait_until("E5 to name the conflicting path",
                   lambda: (lambda x: x if "src/shared.txt" in x.get("detail", "") else None)(
                       delivery_of(srv, "T-3")))

    assert d["needs_you"] is True, d
    assert "src/shared.txt" in d["detail"], d

    assert w.tip("main") == main_before, "main's tip must not move on a conflict"
    assert w.tree("main") == tree_before, "main's tree must not move on a conflict"
    assert w.remote_main() == remote_before, "no remote ref moved"
    assert w.has_branch("ticket/T-3"), "the branch survives a conflict"
    assert os.path.exists(str(wt)), "the worktree survives a conflict"
    assert w.tip("ticket/T-3") == branch_before, (
        "the failed merge is aborted, so the branch's own tip is where it was")
    assert w.git("status", "--porcelain", cwd=wt) == "", (
        "nothing half-applies: a tree carrying conflict markers looks current and is not")


# ── criterion 4 ───────────────────────────────────────────────────────────────────────────────

def test_a_fail_a_verdict_only_on_main_and_a_self_passed_verdict_all_land_nothing(world, serve):
    """MANDATORY NEGATIVE — nothing lands that a separate session did not pass on the branch.

    §1 criterion 4. Three refusals, each leaving `main`, the remote and the branch untouched, and
    each distinguishable from the other two in what the tool says about it.
    """
    w = world

    ticket_on_main(w, "T-4A")
    qa_branch(w, "T-4A", work_branch(w, "T-4A"), verdict="FAIL")

    # (b) the state that exists after EVERY landing — the verdict and the pass on main, the branch
    # carrying neither. A tool that read main would re-fire on this forever.
    ticket_on_main(w, "T-4B")
    wt_b = w.worktree("ticket/T-4B")
    w.write("src/T-4B-left-behind.txt", "an old lane, still on disk\n", cwd=wt_b)
    w.git.commit("T-4B: work that predates the landing", who=MEMBER, cwd=wt_b)
    set_status(w, "T-4B", "pass", w.root)
    w.write(f"{RPT_DIR}/T-4B-qa.md", verdict_text("T-4B"))
    w.write(f"{RPT_DIR}/T-4B-report.md", "# T-4B — report\n")
    w.git.commit("T-4B: the landed ticket, as main carries it", who=MERGER)

    ticket_on_main(w, "T-4C")
    self_passed(w, "T-4C", work_branch(w, "T-4C"))

    w.push_main()
    main_before = w.tip("main")
    remote_before = w.remote_main()
    tips = {t: w.tip(f"ticket/{t}") for t in ("T-4A", "T-4B", "T-4C")}

    srv = serve_fast(serve, w.root)
    reasons = wait_until(
        "the tool to say something about all three",
        lambda: (lambda said: said if all(any(t in line for line in said)
                                          for t in ("T-4A", "T-4B", "T-4C")) else None)(
            land_lines(srv)))
    settle()

    assert w.tip("main") == main_before, (
        "nothing may land:\n" + w.git("log", "--oneline", f"{main_before}..main"))
    assert w.remote_main() == remote_before, "no remote ref moved"
    for tid, tip in tips.items():
        assert w.has_branch(f"ticket/{tid}"), f"ticket/{tid} must survive a refusal"
        assert w.tip(f"ticket/{tid}") == tip, f"ticket/{tid}'s tip must not move"

    said = {}
    for tid in ("T-4A", "T-4B", "T-4C"):
        for line in reasons:
            if tid in line:
                said[tid] = line
                break
    assert len(said) == 3 and len(set(said.values())) == 3, (
        f"each refusal must be distinguishable from the other two: {said}")
    assert "PASS" in said["T-4A"], f"(a) says the verdict does not say PASS: {said['T-4A']}"
    assert "verdict" in said["T-4B"], f"(b) says the branch carries no verdict: {said['T-4B']}"
    assert "ref" in said["T-4C"] or "role" in said["T-4C"], (
        f"(c) says nothing but the run branch carries it: {said['T-4C']}")


# ── criterion 5 ───────────────────────────────────────────────────────────────────────────────

def test_no_force_ever_and_a_branch_survives_unless_its_tree_is_on_main(world, serve):
    """MANDATORY NEGATIVE — the landing can never lose work.

    §1 criterion 5. One repository carrying all four shapes above plus the reap shapes, so the
    argv enumeration is swept over everything `land` spawns, and the reap is asserted where the
    tree rule and each half of the cleanliness rule decide the answer on their own.
    """
    w = world

    ticket_on_main(w, "T-5A")
    wt_a = work_branch(w, "T-5A")
    qa_branch(w, "T-5A", wt_a)

    # a bystander: one change that was uncommitted in its checkout and then committed. Nothing
    # lands it, and nothing may delete it either.
    ticket_on_main(w, "T-5KEEP")
    wt_keep = w.worktree("ticket/T-5KEEP")
    w.write("src/T-5KEEP.txt", "work in progress\n", cwd=wt_keep)
    assert w.git("status", "--porcelain", cwd=wt_keep) != "", "uncommitted, and then committed"
    w.git.commit("T-5KEEP: the round so far", who=MEMBER, cwd=wt_keep)

    # E13's two halves: a checkout holding uncommitted work, and one holding only ignored content.
    ticket_on_main(w, "T-5DIRTY")
    wt_dirty = work_branch(w, "T-5DIRTY")
    qa_branch(w, "T-5DIRTY", wt_dirty)
    w.write("src/uncommitted.txt", "a person was in the middle of something\n", cwd=wt_dirty)

    ticket_on_main(w, "T-5IGN")
    wt_ign = work_branch(w, "T-5IGN")
    w.write(".gitignore", IGNORES + "build/\n", cwd=wt_ign)
    w.git.commit("T-5IGN: ignore the build directory", who=MEMBER, cwd=wt_ign)
    qa_branch(w, "T-5IGN", wt_ign)
    w.write("build/artefact.o", "bytes\n", cwd=wt_ign)

    # the three refusals, so the sweep reaches their argv too
    ticket_on_main(w, "T-5FAIL")
    qa_branch(w, "T-5FAIL", work_branch(w, "T-5FAIL"), verdict="FAIL")
    ticket_on_main(w, "T-5SELF")
    self_passed(w, "T-5SELF", work_branch(w, "T-5SELF"))
    ticket_on_main(w, "T-5CONF")
    w.write("src/contested.txt", "one\ntwo\nthree\n")
    w.git.commit("the file both sides touch", who=MEMBER)
    wt_conf = w.worktree("ticket/T-5CONF")
    w.write("src/contested.txt", "one\nBRANCH\nthree\n", cwd=wt_conf)
    w.git.commit("T-5CONF: the branch's line", who=MEMBER, cwd=wt_conf)
    qa_branch(w, "T-5CONF", wt_conf)
    w.write("src/contested.txt", "one\nMAIN\nthree\n")
    w.git.commit("somebody else's line, on main", who=MERGER)

    w.push_main()
    keep_tip = w.tip("ticket/T-5KEEP")
    before = w.tip("main")

    srv = serve_fast(serve, w.root)
    wait_until("the three landable tickets to reach main",
               lambda: w.git("rev-list", "--count", f"{before}..main") == "3", timeout=90)
    wait_until("T-5A to be reaped", lambda: not w.has_branch("ticket/T-5A"))
    settle()

    # ── the tree rule, on a branch that DID land ──────────────────────────────────────────────
    # `main` carries one commit the remote does not, so the squash of this branch produces a tree
    # that is not the branch's own — and a branch whose tree is not on main is never deleted.
    ticket_on_main(w, "T-5TREE")
    wt_tree = w.worktree("ticket/T-5TREE")
    w.write("src/only-on-main.txt", "a change the branch never saw\n")
    w.git.commit("OTHER: a change on main that is not pushed", who=MERGER)
    w.write("src/T-5TREE.txt", "T-5TREE did something\n", cwd=wt_tree)
    w.git.commit("T-5TREE: the work", who=MEMBER, cwd=wt_tree)
    w.write(f"{RPT_DIR}/T-5TREE-report.md", "# T-5TREE — report\n", cwd=wt_tree)
    w.git.commit("T-5TREE: the report", who=MEMBER, cwd=wt_tree)
    qa_branch(w, "T-5TREE", wt_tree)

    wait_until("T-5TREE to reach main",
               lambda: (w.root / "src" / "T-5TREE.txt").exists(), timeout=90)
    settle()
    assert w.has_branch("ticket/T-5TREE"), (
        "a landed branch whose tree is not the tree on main keeps its commits")
    assert w.tree("ticket/T-5TREE") != w.tree("main"), (
        "and that is why: main carries a change the branch does not")
    assert (w.root / "src" / "only-on-main.txt").exists(), "and nothing on main was lost"
    assert os.path.exists(str(wt_tree)), "a branch that is kept keeps its checkout"

    # ── the sweep ─────────────────────────────────────────────────────────────────────────────
    argvs = spawned(srv)
    assert argvs, "the product logged no argv at all, so this case has examined nothing"
    scratch_removals = [argv for argv in argvs if a_scratch_removal(argv)]
    assert scratch_removals, (
        "the product removed no scratch worktree at all, so the exception carved out below is a "
        "hole nothing walks through — check `a_scratch_removal` before believing this sweep")
    for argv in argvs:
        assert argv and argv[0] == "git", f"land spawned something that is not git: {argv}"
        if a_scratch_removal(argv):
            continue
        for token in argv:
            assert not token.startswith("--force"), f"a forced invocation: {argv}"
            assert token not in FORCE_TOKENS, f"a forced invocation: {argv}"
        if "push" in argv:
            assert "--delete" not in argv and "-d" not in argv, f"a deleting push: {argv}"
            for token in argv[argv.index("push") + 1:]:
                assert not token.startswith("+"), f"a forced refspec: {argv}"

    # ── the branch that was deleted, and the bystander that was not ───────────────────────────
    assert not w.has_branch("ticket/T-5A"), "a landed branch whose tree is on main is reaped"
    assert not os.path.exists(str(wt_a)), "and so is its checkout"
    assert w.has_branch("ticket/T-5KEEP"), "a branch nothing landed is never deleted"
    assert w.tip("ticket/T-5KEEP") == keep_tip, "and its tip does not move"
    assert os.path.exists(str(wt_keep)), "and neither does its checkout"

    # ── the cleanliness rule, each half on its own ────────────────────────────────────────────
    assert os.path.exists(str(wt_dirty)), "a checkout holding uncommitted work is kept"
    assert (wt_dirty / "src" / "uncommitted.txt").exists(), "and the work in it is still there"
    assert os.path.exists(str(wt_ign)), "a checkout holding ignored content is kept"
    assert (wt_ign / "build" / "artefact.o").exists(), "and so is the ignored content"

    lines = land_lines(srv)
    dirty_said = [x for x in lines if "T-5DIRTY" in x and "kept" in x]
    ign_said = [x for x in lines if "T-5IGN" in x and "kept" in x]
    assert dirty_said, f"the tool must say it kept T-5DIRTY's checkout:\n" + "\n".join(lines)
    assert ign_said, f"the tool must say it kept T-5IGN's checkout:\n" + "\n".join(lines)
    assert any("uncommitted" in x for x in dirty_said), (
        f"and which half refused — uncommitted: {dirty_said}")
    assert any("ignored" in x for x in ign_said), (
        f"and which half refused — ignored: {ign_said}")
    assert not any("ignored" in x for x in dirty_said), (
        f"the two halves are distinguishable: {dirty_said}")

    # ── and nothing the refusals guard reached main ───────────────────────────────────────────
    for tid in ("T-5FAIL", "T-5SELF", "T-5CONF"):
        assert w.has_branch(f"ticket/{tid}"), f"ticket/{tid} must survive"
    for tid in ("T-5FAIL", "T-5SELF"):
        assert not (w.root / "src" / f"{tid}.txt").exists(), f"{tid} must not have reached main"
    assert (w.root / "src" / "contested.txt").read_text() == "one\nMAIN\nthree\n", (
        "the conflicting branch's line must not be on main")


# ── round 2 · transcribed from QA's own reproductions ─────────────────────────────────────────
#
# Three cases, each built from the commands and fixture state in a QA verdict rather than from a
# criterion's wording. `docs/reports/SRV-FEAT-437/SRV-FEAT-437-qa-break-probe.py`,
# `-qa-break-probe3.py` and `-qa2-break-probe.py` are the probes they come from. All three are
# criterion 4 — the mandatory negative — by three mechanisms, and fixing any one of them on its own
# leaves the other two live.

def test_a_pushed_run_branch_is_not_a_second_session(world, serve):
    """A self-passed branch does not become somebody else's work by being pushed.

    §1 criterion 4(c). Two QA sessions found this independently: the guard skipped exactly one ref
    name, `refs/heads/<branch>`, so `refs/remotes/origin/ticket/<id>` — the run branch's own remote
    mirror, written by the same session — read as a second session, and the verdict the implementer
    wrote for itself landed, was pushed and was reaped. The control and the failing case differ by
    one `git push`; the other two members differ by one `git branch`.

    Transcribed from `SRV-FEAT-437-qa2-break-probe.py` (the push) and widened to every other way one
    branch wears a second name, because a rule that excludes only the pushed mirror is a rule about
    the mirror rather than about the branch.
    """
    w = world
    seen = {}
    for tid, second_name in (("T-CTL", None),
                             ("T-PUSH", "push"),
                             ("T-MIRROR", "branch-at-the-same-tip"),
                             ("T-REMOTE", "push-under-another-remote-name")):
        ticket_on_main(w, tid)
        self_passed(w, tid, work_branch(w, tid))
        if second_name == "push":
            w.git("push", "origin", f"ticket/{tid}")
        elif second_name == "branch-at-the-same-tip":
            w.git("branch", f"mirror/{tid}", f"ticket/{tid}")
        elif second_name == "push-under-another-remote-name":
            w.git("push", "origin", f"ticket/{tid}:refs/heads/somebody-elses/{tid}")
        seen[tid] = w.tip(f"ticket/{tid}")
    w.push_main()

    before = w.tip("main")
    remote_before = w.remote_main()

    srv = serve_fast(serve, w.root)
    wait_until("the tool to have judged every one of them",
               lambda: all(any(t in line for line in land_lines(srv)) for t in seen))
    settle(8)

    assert w.tip("main") == before, (
        "not one of these was passed by a second session, so not one may land; main gained:\n"
        + w.git("log", "--oneline", f"{before}..main"))
    assert w.remote_main() == remote_before, "and no remote ref may move"
    for tid, tip in seen.items():
        assert w.has_branch(f"ticket/{tid}"), f"ticket/{tid} must survive"
        assert w.tip(f"ticket/{tid}") == tip, f"ticket/{tid}'s tip must not move"

    # and every one of them is refused for the SAME stated reason — the control included, because
    # a control refused for one reason and a pushed branch refused for another is two rules.
    # Round 3 made that reason narrower: not "no other ref carries it" but "no session other than
    # the run session is recorded for it", which is the question §1 actually asks.
    said = {tid: [line for line in land_lines(srv) if tid in line] for tid in seen}
    for tid, lines in said.items():
        assert lines, f"the tool said nothing about {tid}"
        assert any("no role session recorded" in line for line in lines), (
            f"{tid} must be refused because no second session ran, not for some other reason "
            f"and not because of a ref: {lines}")


def test_a_heading_that_names_both_a_pass_and_a_fail_lands_nothing(world, serve):
    """A verdict whose heading names more than one round is not a decision the tool may take.

    §1 criterion 4(a). `docs/reports/SRV-FIX-131-qa.md` and `SRV-FIX-293-qa.md` really carry
    `# <id> — QA verdict — round 1 FAIL · round 2 **PASS**`, so the multi-round heading is this
    repository's own convention — and the same convention runs the other way as often as a ticket
    fails on its second round. The rule was a bare `find("PASS")`, so the FAIL below landed.

    Transcribed from `SRV-FEAT-437-qa-break-probe.py`. Both orders are asserted: a tool that reads
    the LAST word instead of the first is still guessing, and the ticket the guess is wrong about
    is the one it merges.
    """
    w = world
    for tid, heading in (("T-H1", "round 1 PASS · round 2 **FAIL**"),
                         ("T-H2", "round 1 FAIL · round 2 **PASS**")):
        ticket_on_main(w, tid)
        qa_branch(w, tid, work_branch(w, tid), verdict=heading)
    w.push_main()

    before = w.tip("main")
    remote_before = w.remote_main()
    srv = serve_fast(serve, w.root)
    wait_until("the tool to have judged both headings",
               lambda: all(any(t in line for line in land_lines(srv)) for t in ("T-H1", "T-H2")))
    settle(8)

    assert w.tip("main") == before, (
        "a heading that names both outcomes decides nothing, and neither of these may land; "
        "main gained:\n" + w.git("log", "--oneline", f"{before}..main"))
    assert w.remote_main() == remote_before, "and no remote ref may move"
    for tid in ("T-H1", "T-H2"):
        assert w.has_branch(f"ticket/{tid}"), f"ticket/{tid} must survive"
        lines = [line for line in land_lines(srv) if tid in line]
        assert any("PASS" in line and "FAIL" in line for line in lines), (
            f"the tool must say the heading names both, so a person can settle it: {lines}")


def test_a_kept_earlier_round_never_decides_for_the_live_verdict(world, serve):
    """The verdict is the document the workflow names, and a kept round beside it changes nothing.

    §1 criterion 4(a). `<id>-qa*.md` is a SET, and the tool took the first entry in `ls-tree` order.
    Git sorts `-` (0x2D) before `.` (0x2E), so a kept `<id>-qa-round1.md` was read instead of the
    live `<id>-qa.md` and a FAIL was never opened — the tool logged `landed`, not `does not say
    PASS`.

    Transcribed from `SRV-FEAT-437-qa-break-probe3.py`, with the mirror case beside it: an earlier
    round that FAILED must not stop a ticket whose live verdict passes, or the fix trades one wrong
    answer for another.
    """
    w = world
    ticket_on_main(w, "T-K1")
    wt1 = work_branch(w, "T-K1")
    qa_branch(w, "T-K1", wt1, verdict="FAIL")
    w.write(f"{RPT_DIR}/T-K1-qa-round1.md", verdict_text("T-K1", "PASS WITH MINORS"), cwd=wt1)
    w.git.commit("T-K1: keep round 1's verdict beside round 2's", who=MEMBER, cwd=wt1)

    ticket_on_main(w, "T-K2")
    wt2 = work_branch(w, "T-K2")
    qa_branch(w, "T-K2", wt2)
    w.write(f"{RPT_DIR}/T-K2-qa-round1.md", verdict_text("T-K2", "FAIL"), cwd=wt2)
    w.git.commit("T-K2: keep round 1's FAIL beside round 2's pass", who=MEMBER, cwd=wt2)
    w.push_main()

    before = w.tip("main")
    srv = serve_fast(serve, w.root)
    wait_until("T-K2, whose live verdict passes, to land",
               lambda: (w.root / "src" / "T-K2.txt").exists(), timeout=90)
    settle(8)

    assert w.git("rev-list", "--count", f"{before}..main") == "1", (
        "exactly one of the two may land:\n" + w.git("log", "--oneline", f"{before}..main"))
    assert not (w.root / "src" / "T-K1.txt").exists(), (
        "T-K1's live verdict is FAIL, and a round-1 document kept beside it does not overrule it")
    assert w.has_branch("ticket/T-K1"), "T-K1's branch must survive"
    lines = [line for line in land_lines(srv) if "T-K1" in line]
    assert any("PASS" in line for line in lines), (
        f"the tool must say T-K1's verdict does not say PASS, having actually read it: {lines}")


# ── SRV-FIX-607 · the latest round decides ─────────────────────────────────────────────────────
#
# The kit's `qa-ticket` skill writes round 1 to `<id>-qa.md` and round N to `<id>-qa-round<N>.md`,
# keeping every earlier round (example-platform `.claude/skills/qa-ticket/SKILL.md:28-29`), and the
# kit's plan tool reads the highest round. Each round below is a separate QA session the tool
# recorded, on `ticket/<id>-qa` cut from the run branch's tip at claim, as a real QA lane is.

def verdict_name(tid, n):
    return f"{tid}-qa.md" if n == 1 else f"{tid}-qa-round{n}.md"


def qa_round(w, tid, n, verdict, *, status=None):
    """One QA round the kit's skill writes, in a QA session the tool dispatched and recorded."""
    branch = f"ticket/{tid}-qa"
    w.git("branch", "-f" if w.has_branch(branch) else "--no-track", branch, f"ticket/{tid}")
    path = w.root / ".factory" / "worktrees" / branch.replace("/", "_")
    path.parent.mkdir(parents=True, exist_ok=True)
    w.git("worktree", "add", str(path), branch)
    w.write(f"{RPT_DIR}/{verdict_name(tid, n)}", verdict_text(tid, verdict), cwd=path)
    rewrite_status(w, tid, status or ("fail" if "FAIL" in verdict else "pass"), path)
    w.git.commit(f"{tid}: QA verdict — round {n} — {verdict}", who=REVIEWER, cwd=path)
    tip = w.tip("HEAD", cwd=path)
    w.git("worktree", "remove", str(path))
    session_left(w, tid, "qa", branch, tip, run_id=f"run-{tid}-qa-round{n}")
    return tip


def fix_after_round(w, tid, wt, n):
    """The implementer takes round n's verdict back onto the run branch and fixes what it found."""
    w.git("-c", f"user.name={MEMBER[0]}", "-c", f"user.email={MEMBER[1]}",
          "merge", "--no-ff", "--no-edit", "--no-gpg-sign", f"ticket/{tid}-qa", cwd=wt)
    w.write(f"src/{tid}-fix{n}.txt", f"what round {n} found, fixed\n", cwd=wt)
    w.git.commit(f"{tid}: fix what round {n} found", who=MEMBER, cwd=wt)


def heading_at(w, ref, tid, n):
    return w.git("show", f"{ref}:{RPT_DIR}/{verdict_name(tid, n)}").splitlines()[0]


def test_the_latest_round_verdict_lands(world, serve):
    """§1 criterion 1 — round 1 FAILED, round 2 PASSED, and the ticket lands on round 2's word.

    The two verdicts disagree on the branch the tool reads, so a lander that opens `<id>-qa.md`
    reads a FAIL and lands nothing, and one that opens the latest round reads a PASS and lands.
    """
    w = world
    ticket_on_main(w, "T-R2")
    wt = work_branch(w, "T-R2")
    qa_round(w, "T-R2", 1, "FAIL")
    fix_after_round(w, "T-R2", wt, 1)
    qa_tip = qa_round(w, "T-R2", 2, "PASS WITH MINORS")
    w.push_main()

    assert "FAIL" in heading_at(w, "ticket/T-R2-qa", "T-R2", 1), (
        "round 1's FAIL must be on the branch the tool reads, or this case cannot tell the rounds "
        "apart")
    assert "PASS" in heading_at(w, "ticket/T-R2-qa", "T-R2", 2), "and round 2's PASS beside it"
    assert not w.git.ok("cat-file", "-e", f"ticket/T-R2:{RPT_DIR}/{verdict_name('T-R2', 2)}"), (
        "round 2's verdict is on the QA branch alone, as a QA lane leaves it")

    before = w.tip("main")
    qa_tree = w.tree(qa_tip)
    srv = serve_fast(serve, w.root)
    with_what_it_said(srv, "T-R2", "T-R2 to land on round 2's PASS",
                      lambda: w.tip("main") != before)
    settle(4)

    assert w.git("rev-list", "--count", f"{before}..main") == "1", (
        w.git("log", "--oneline", f"{before}..main"))
    assert w.tree("main") == qa_tree, "main carries the QA branch's tree, round 2's verdict in it"
    assert w.git("log", "-1", "--format=%s", "main").startswith("T-R2: ")
    said = [x for x in land_lines(srv) if "T-R2" in x]
    assert any("landed" in x for x in said), said


def test_rounds_compare_as_integers(world, serve):
    """§1 criterion 2 — round 10 is later than round 9, and round 2 is later than `-qa.md`.

    Both directions on one server: as text, `-qa-round9.md` sorts after `-qa-round10.md` (and
    `ls-tree` lists `-qa-round10.md` first), so a string comparison lands neither or the wrong one.
    """
    w = world
    ticket_on_main(w, "T-R10")
    wt = work_branch(w, "T-R10")
    qa_round(w, "T-R10", 1, "FAIL")
    fix_after_round(w, "T-R10", wt, 1)
    qa_round(w, "T-R10", 9, "FAIL")
    fix_after_round(w, "T-R10", wt, 9)
    qa_round(w, "T-R10", 10, "PASS")

    ticket_on_main(w, "T-R2F")
    wt2 = work_branch(w, "T-R2F")
    qa_round(w, "T-R2F", 1, "PASS")
    fix_after_round(w, "T-R2F", wt2, 1)
    qa_round(w, "T-R2F", 2, "FAIL")
    w.push_main()

    assert "FAIL" in heading_at(w, "ticket/T-R10-qa", "T-R10", 9)
    assert "PASS" in heading_at(w, "ticket/T-R10-qa", "T-R10", 10)
    assert "PASS" in heading_at(w, "ticket/T-R2F-qa", "T-R2F", 1)
    assert "FAIL" in heading_at(w, "ticket/T-R2F-qa", "T-R2F", 2)

    before = w.tip("main")
    srv = serve_fast(serve, w.root)
    with_what_it_said(srv, "T-R10", "T-R10 to land on round 10's PASS",
                      lambda: (w.root / "src" / "T-R10.txt").exists())
    round2 = f"{RPT_DIR}/{verdict_name('T-R2F', 2)}"
    with_what_it_said(srv, "T-R2F", "the tool to refuse T-R2F on round 2's FAIL, naming it",
                      lambda: [x for x in land_lines(srv)
                               if "T-R2F" in x and round2 in x and "does not say PASS" in x])
    settle(6)

    assert w.git("rev-list", "--count", f"{before}..main") == "1", (
        "exactly one of the two may land:\n" + w.git("log", "--oneline", f"{before}..main"))
    assert not (w.root / "src" / "T-R2F.txt").exists(), (
        "T-R2F's latest round FAILED; round 1's PASS in `-qa.md` does not land it")
    assert w.has_branch("ticket/T-R2F"), "a refused branch survives"


def test_a_later_fail_or_a_sessionless_round_refuses(world, serve):
    """§1 criterion 3, MANDATORY NEGATIVE — reading the latest round never weakens a refusal.

    T-LF: rounds 1 and 2 PASSED and round 3 FAILED, every one by a recorded QA session, and the
    ticket says `status: pass`: the two earlier PASSes never override the later FAIL. T-SL: round 1 FAILED in a recorded QA session,
    then the implementer wrote `-qa-round2.md` PASS and `status: pass` on the run branch itself:
    the latest round is read, and no session left it, so it is refused by the receipt rule.
    """
    w = world
    ticket_on_main(w, "T-LF")
    wt = work_branch(w, "T-LF")
    qa_round(w, "T-LF", 1, "PASS")
    fix_after_round(w, "T-LF", wt, 1)
    qa_round(w, "T-LF", 2, "PASS")
    fix_after_round(w, "T-LF", wt, 2)
    # round 3's session left `status: pass` beside its FAIL, so only the verdict's reading stands
    # between this ticket and `main` — the status rule cannot refuse it on the reader's behalf
    qa_round(w, "T-LF", 3, "FAIL", status="pass")

    ticket_on_main(w, "T-SL")
    wt2 = work_branch(w, "T-SL")
    qa_round(w, "T-SL", 1, "FAIL")
    fix_after_round(w, "T-SL", wt2, 1)
    w.write(f"{RPT_DIR}/{verdict_name('T-SL', 2)}", verdict_text("T-SL", "PASS"), cwd=wt2)
    rewrite_status(w, "T-SL", "pass", wt2)
    w.git.commit("T-SL: QA verdict — round 2 — PASS", who=MEMBER, cwd=wt2)
    w.push_main()

    assert w.git.ok("merge-base", "--is-ancestor", "ticket/T-SL-qa", "ticket/T-SL"), (
        "the run branch descends from the QA branch, so it is the branch the tool reads")
    assert "PASS" in heading_at(w, "ticket/T-SL", "T-SL", 2)

    before = w.tip("main")
    remote_before = w.remote_main()
    srv = serve_fast(serve, w.root)
    round3 = f"{RPT_DIR}/{verdict_name('T-LF', 3)}"
    with_what_it_said(srv, "T-LF", "the tool to refuse T-LF on round 3's FAIL, naming it",
                      lambda: [x for x in land_lines(srv)
                               if "T-LF" in x and round3 in x and "does not say PASS" in x])
    d = with_what_it_said(
        srv, "T-SL", "E5 to carry the receipt refusal for T-SL's sessionless round 2",
        lambda: (lambda x: x if "a ref that carries a document is not a session"
                 in x.get("detail", "") else None)(delivery_of(srv, "T-SL")))
    settle(4)

    assert w.tip("main") == before, w.git("log", "--oneline", f"{before}..main")
    assert w.remote_main() == remote_before, "nothing was pushed"
    assert w.has_branch("ticket/T-LF") and w.has_branch("ticket/T-SL")
    assert "no session recorded for T-SL" in d["detail"], d


# ── round 3 · transcribed from QA round 2's probes ────────────────────────────────────────────
#
# Two QA sessions judged round 2 and between them found three blockers. The first two are the
# separate-session guard, wrong in both directions at once; the third is the landing writing into
# the operator's own checkout. `SRV-FEAT-437-qa3-break-probe.py`,
# `SRV-FEAT-437-qa2r-probe-diverged-main.py` and `-qa2r-probe-jam.py` are where these come from.


def test_a_fast_forwarded_role_branch_is_still_a_second_session(world, serve):
    """A verdict a second session really wrote lands, however it reached the run branch.

    §1 criterion 1. The guard skipped any candidate role branch standing at the run branch's own
    tip — and bringing a role branch back with `git merge` IS a fast-forward whenever the run
    branch has not moved, which is git's default and the ordinary case. So a real QA verdict never
    landed, and the refusal said no role branch carried it while `ticket/<id>-qa` sat there
    carrying it. The shipped fixture missed it by one flag: `qa_branch()` merges `--no-ff`.

    From `SRV-FEAT-437-qa3-break-probe.py::test_probe1_a_fast_forwarded_qa_branch_never_lands`.
    """
    w = world
    ticket_on_main(w, "T-FF")
    wt = work_branch(w, "T-FF")
    qa_wt = w.worktree("ticket/T-FF-qa", base="ticket/T-FF")
    w.write(f"{RPT_DIR}/T-FF-qa.md", verdict_text("T-FF"), cwd=qa_wt)
    set_status(w, "T-FF", "pass", qa_wt)
    w.git.commit("T-FF: QA verdict — PASS WITH MINORS", who=REVIEWER, cwd=qa_wt)
    w.git("worktree", "remove", str(qa_wt))
    w.git("merge", "--no-edit", "ticket/T-FF-qa", cwd=wt)
    run_id = plant_role_run(w, "T-FF", "qa")
    w.push_main()

    assert w.tip("ticket/T-FF") == w.tip("ticket/T-FF-qa"), (
        "the merge must be a fast-forward, or this is the case the suite already had")
    assert w.git("log", "-1", "--format=%an", "ticket/T-FF") == REVIEWER[0], (
        "and a second session must be the one that wrote the verdict commit")

    before = w.tip("main")
    srv = serve_fast(serve, w.root)
    read_back_by_the_product(srv, run_id)
    wait_until("T-FF to land", lambda: (w.root / "src" / "T-FF.txt").exists(), timeout=90)
    settle(8)

    assert w.git("rev-list", "--count", f"{before}..main") == "1", (
        "a ticket a separate session passed lands, and it lands once:\n"
        + w.git("log", "--oneline", f"{before}..main"))
    assert not w.has_branch("ticket/T-FF"), "and its branch is reaped"


def test_a_decoy_branch_under_the_run_lanes_own_name_is_not_a_second_session(world, serve):
    """One `git branch` by the implementing session does not make its own verdict somebody else's.

    §1 criterion 4(c). Round 1's hole was `refs/remotes/origin/ticket/<id>`; round 2's was any
    `refs/heads/<branch>-<anything>` at another tip. Both are one undrawn model — the guard asked
    *does another ref carry this document* where §1 asks *did another session write it*. So this
    case is the ratchet on the answer rather than on the namespace: no session is recorded for
    this ticket in any role, so nothing lands whatever refs exist — including a branch wearing the
    role branch's own name.

    From `SRV-FEAT-437-qa3-break-probe.py::test_probe2_a_decoy_branch_makes_a_self_pass_land`.
    """
    w = world
    ticket_on_main(w, "T-DECOY")
    wt = work_branch(w, "T-DECOY")
    self_passed(w, "T-DECOY", wt)
    w.git("-c", f"user.name={MEMBER[0]}", "-c", f"user.email={MEMBER[1]}",
          "commit", "--allow-empty", "--no-gpg-sign", "-m", "T-DECOY: a later empty commit",
          cwd=wt)
    w.git("branch", "ticket/T-DECOY-keep", "ticket/T-DECOY~1")
    w.git("branch", "ticket/T-DECOY-qa", "ticket/T-DECOY~1")
    w.push_main()

    assert w.git.ok("rev-parse", "ticket/T-DECOY-keep:docs/reports/T-DECOY-qa.md"), (
        "the decoy must really carry the verdict document, or it tests nothing")
    assert w.git.ok("rev-parse", "ticket/T-DECOY-qa:docs/reports/T-DECOY-qa.md"), (
        "and so must one wearing the role branch's own name, because a name is not a session")

    before = w.tip("main")
    remote_before = w.remote_main()
    tip = w.tip("ticket/T-DECOY")
    srv = serve_fast(serve, w.root)
    wait_until("the tool to judge T-DECOY",
               lambda: [x for x in land_lines(srv) if "T-DECOY" in x], timeout=90)
    settle(8)

    said = [x for x in land_lines(srv) if "T-DECOY" in x]
    assert w.tip("main") == before, (
        "nobody but the implementing session ever ran, so nothing may land:\n"
        + w.git("log", "--oneline", f"{before}..main") + "\n" + "\n".join(said))
    assert w.remote_main() == remote_before, "and no remote ref may move"
    assert w.has_branch("ticket/T-DECOY"), "and the branch survives"
    assert w.tip("ticket/T-DECOY") == tip, "with its tip where it was"
    assert any("no role session recorded" in x for x in said), (
        f"and the refusal names what is actually missing — a session, not a ref: {said}")


def test_a_conflicting_landing_leaves_the_operators_checkout_untouched(world, serve):
    """A conflict stops the landing without writing a single byte into the operator's checkout.

    §1 criterion 3, and D3's *"a conflict is a `needs you` and never a broken `main`"*. The landing
    squashed inside the operator's own primary checkout, so a conflicting squash left it mid-merge
    — a staged half-merge and `UU` markers in their working tree — and because the tool then
    refuses while that checkout is dirty, **one conflict jammed every later landing** until a
    person repaired it by hand. Both halves are asserted here: the checkout is byte-identical
    afterwards, and the innocent ticket behind it still lands.

    The conflict has to happen in the SQUASH rather than in the merge-in, which is why `main` moves
    here and is NOT pushed: `origin/main` is then an ancestor of the branch, the merge-in is a
    no-op, and the collision lands where the shipped case never looked.

    From `SRV-FEAT-437-qa2r-probe-diverged-main.py` and `SRV-FEAT-437-qa2r-probe-jam.py`.
    """
    w = world

    ticket_on_main(w, "T-J1")
    w.write("src/ledger.txt", "one\ntwo\nthree\n")
    w.git.commit("the ledger both sides append to", who=MEMBER)
    w.push_main()
    wt1 = w.worktree("ticket/T-J1")
    w.write("src/ledger.txt", "one\nBRANCH\nthree\n", cwd=wt1)
    w.git.commit("T-J1: the branch's row", who=MEMBER, cwd=wt1)
    w.write(f"{RPT_DIR}/T-J1-report.md", "# T-J1 — report\n", cwd=wt1)
    w.git.commit("T-J1: the report", who=MEMBER, cwd=wt1)
    qa_branch(w, "T-J1", wt1)

    ticket_on_main(w, "T-J2")
    qa_branch(w, "T-J2", work_branch(w, "T-J2"))

    w.write("src/ledger.txt", "one\nMAIN\nthree\n")
    w.git.commit("somebody else's row, landed here and not yet pushed", who=MERGER)

    before = w.tip("main")
    checkout_before = w.git("status", "--porcelain")
    head_before = w.git("rev-parse", "HEAD")
    assert checkout_before == "", "the operator's checkout starts clean"

    srv = serve_fast(serve, w.root)
    wait_until("the tool to have judged both",
               lambda: all(any(t in line for line in land_lines(srv)) for t in ("T-J1", "T-J2")),
               timeout=90)
    wait_until("T-J2, which conflicts with nothing, to land",
               lambda: (w.root / "src" / "T-J2.txt").exists(), timeout=90)
    settle(8)

    said = land_lines(srv)
    assert not (w.root / "src" / "ledger-BRANCH.txt").exists(), "sanity"
    assert w.git("show", "main:src/ledger.txt") == "one\nMAIN\nthree", (
        "T-J1 conflicts, so its row must not be on main:\n" + "\n".join(said))
    assert w.has_branch("ticket/T-J1"), "and its branch survives"

    # the half the shipped conflict case never looked at
    assert w.git("status", "--porcelain") == checkout_before, (
        "a conflicting landing must leave the operator's own checkout byte-identical. It is now:\n"
        + w.git("status") + "\n" + "\n".join(said))
    assert not (w.root / ".git" / "MERGE_HEAD").exists(), "and no merge left half-done in it"

    # and the innocent ticket behind it is not jammed
    assert w.git("rev-list", "--count", f"{before}..main") == "1", (
        "T-J2 was passed by a separate session and conflicts with nothing; it lands, and the "
        "conflict ahead of it does not jam it:\n" + w.git("log", "--oneline", f"{before}..main")
        + "\n" + "\n".join(said))
    assert w.git("rev-parse", "HEAD") != head_before or w.git("rev-parse", "main") == head_before, (
        "sanity: main moved for T-J2")


# ── round 4 · S7 — the operator's checkout ────────────────────────────────────────────────────
#
# `docs/technical/MODEL.md` §S7: the tool never edits a working tree it did not create, and never moves a ref
# such a tree has checked out unless the tree moves with it; the only way it moves the operator's
# own checkout is `git merge --ff-only`, which refuses rather than overwrite, and when git refuses
# the tool lands nothing, reaps nothing, and says which paths stood in the way.
#
# Three rounds each answered that with a different mechanism and none with a boundary. These cases
# are the boundary. From QA round 3's `SRV-FEAT-437-qa-round3-probe-dirty-checkout.py`.


def checkout_status(w, cwd=None):
    """`git status --porcelain` with its bytes intact — the leading column is the whole point.

    `G.__call__` strips the output, which eats the leading space of ` M path` on the first line
    only, so a comparison against it silently comes out as a staged change.
    """
    out = subprocess.run(["git", "-C", str(cwd or w.root), "status", "--porcelain"],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout


def operator_edits(w, *, tracked, untracked="notes-of-my-own.md"):
    """Leave a person mid-edit in the served checkout, the way D4 says the deployment always is."""
    p = w.root / tracked
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(p.read_text() + "a line I was in the middle of writing\n" if p.exists()
                 else "a line I was in the middle of writing\n")
    (w.root / untracked).write_text("a scratch file of mine\n")
    return checkout_status(w)


def test_a_landing_fast_forwards_the_operators_checkout_and_keeps_their_edits(world, serve):
    """S7 · the ordinary deployment: a person is mid-edit in the checkout the tool serves.

    D4: *"An operator with unrelated edits in flight is not blocked, because a fast-forward that
    touches none of their files succeeds."* Round 3 moved `refs/heads/main` under this checkout with
    `update-ref`, so `HEAD` followed the ref while the index and working tree stayed behind: the
    landing showed as staged deletions, the landed files were never on disk, and the operator's next
    ordinary commit would have reverted the ticket including its verdict.
    """
    w = world
    ticket_on_main(w, "T-FFW")
    qa_branch(w, "T-FFW", work_branch(w, "T-FFW"))
    w.push_main()

    mine = operator_edits(w, tracked="src/base.txt")
    assert mine == " M src/base.txt\n?? notes-of-my-own.md\n", (
        f"the fixture must leave exactly the operator's own two entries, one of them an UNSTAGED "
        f"modification to a tracked file: {mine!r}")
    before = w.tip("main")

    srv = serve_fast(serve, w.root)
    wait_until("T-FFW to land", lambda: w.tip("main") != before, timeout=90)
    settle(8)

    assert w.git("rev-list", "--count", f"{before}..main") == "1", (
        "it lands, and it lands once:\n" + w.git("log", "--oneline", f"{before}..main"))
    assert (w.root / "src" / "T-FFW.txt").exists(), (
        "and the landed file is ON DISK in the operator's checkout — a ref that moved without its "
        "working tree is the round-3 blocker")
    assert w.git("rev-parse", "HEAD") == w.tip("main"), "HEAD and the ref agree"
    assert (w.root / "src" / "base.txt").read_text().endswith(
        "a line I was in the middle of writing\n"), "the operator's edit survives byte for byte"
    assert (w.root / "notes-of-my-own.md").read_text() == "a scratch file of mine\n", (
        "and so does their untracked file")
    assert checkout_status(w) == mine, (
        "and afterwards the checkout shows THEIR two entries and nothing else — round 3 showed the "
        "whole landing here as staged deletions:\n" + w.git("status"))


def test_a_landing_that_would_overwrite_the_operators_edit_lands_nothing(world, serve):
    """S7 · when git refuses, the tool lands nothing, reaps nothing, and names the paths.

    D4: *"An operator editing a file the ticket also changed is asked, once, to commit or set aside
    their edit"*, and `main`'s ref, the remote, the branch and the lane worktree are left exactly as
    they were.
    """
    w = world
    ticket_on_main(w, "T-BLK")
    w.write("src/contested.txt", "as main has it\n")
    w.git.commit("the file the ticket and the operator both touch", who=MEMBER)
    wt = w.worktree("ticket/T-BLK")
    w.write("src/contested.txt", "the branch's version\n", cwd=wt)
    w.git.commit("T-BLK: the work", who=MEMBER, cwd=wt)
    w.write(f"{RPT_DIR}/T-BLK-report.md", "# T-BLK — report\n", cwd=wt)
    w.git.commit("T-BLK: the report", who=MEMBER, cwd=wt)
    qa_branch(w, "T-BLK", wt)
    w.push_main()

    w.write("src/contested.txt", "what I am in the middle of writing\n")
    mine = checkout_status(w)
    assert mine == " M src/contested.txt\n", (
        f"the operator must be mid-edit on a TRACKED file the ticket also changes: {mine!r}")

    before = w.tip("main")
    tree_before = w.tree("main")
    remote_before = w.remote_main()
    branch_before = w.tip("ticket/T-BLK")
    head_before = w.git("rev-parse", "HEAD")

    srv = serve_fast(serve, w.root)
    d = wait_until("E5 to name the path that stood in the way",
                   lambda: (lambda x: x if "src/contested.txt" in x.get("detail", "") else None)(
                       delivery_of(srv, "T-BLK")), timeout=90)
    settle(8)

    assert d["needs_you"] is True, d
    assert w.tip("main") == before, "main's ref is exactly as it was"
    assert w.tree("main") == tree_before, "and so is its tree"
    assert w.git("rev-parse", "HEAD") == head_before, "and so is the checkout's HEAD"
    assert w.remote_main() == remote_before, "the remote is exactly as it was"
    assert w.has_branch("ticket/T-BLK"), "the branch is not reaped"
    assert w.tip("ticket/T-BLK") == branch_before, "and its tip has not moved"
    assert os.path.exists(str(wt)), "the lane worktree is not reaped"
    assert (w.root / "src" / "contested.txt").read_text() == (
        "what I am in the middle of writing\n"), "and the operator's edit is untouched"
    assert checkout_status(w) == mine, "their checkout is byte-identical"


def test_a_base_no_working_tree_holds_is_moved_by_update_ref(world, serve):
    """S7's carve-out: `update-ref` is allowed exactly when no foreign tree holds the base.

    The served repository's own checkout is on another branch, so nothing holds `main` and there is
    no tree to carry along. Round 3 asked `rev-parse --abbrev-ref HEAD` in one directory, which
    answers this case correctly by accident and the case above wrongly.
    """
    w = world
    ticket_on_main(w, "T-URF")
    qa_branch(w, "T-URF", work_branch(w, "T-URF"))
    w.push_main()

    w.git("checkout", "-q", "-b", "somewhere-else")
    assert w.git("rev-parse", "--abbrev-ref", "HEAD") == "somewhere-else"
    holders = [line for line in w.git("worktree", "list", "--porcelain").splitlines()
               if line == "branch refs/heads/main"]
    assert not holders, f"no working tree may hold main for this case: {holders}"

    before = w.tip("main")
    srv = serve_fast(serve, w.root)
    wait_until("T-URF to land", lambda: w.tip("main") != before, timeout=90)
    settle(8)

    assert w.git("rev-list", "--count", f"{before}..main") == "1", (
        "main moves even though nobody has it checked out:\n"
        + w.git("log", "--oneline", f"{before}..main"))
    assert w.remote_main() == w.tip("main"), "and the remote has it"
    assert not w.has_branch("ticket/T-URF"), "and the branch is reaped"
    assert w.git("rev-parse", "--abbrev-ref", "HEAD") == "somewhere-else", (
        "and the operator's checkout was not moved off the branch they were on")
    assert checkout_status(w) == "", "nor left dirty"


# ── SRV-FIX-475 · the branch the verdict is READ from ─────────────────────────────────────────
#
# `SRV-FEAT-437` read the verdict, its heading and the ticket's `status:` from the run branch and
# from nothing else, so the ordinary state after every QA round this repository runs — the verdict
# committed on the QA lane's OWN branch, the run branch untouched because the QA lane cannot move a
# ref another worktree has checked out — landed nothing. On 2026-09-08 three tickets sat at PASS and
# a person moved all three by hand. Every world below forces the run branch and the role branch to
# disagree before it asserts anything, because a fixture in which the two hold the same document
# passes identically whichever one the tool read — which is exactly how the whole class escaped.


def rewrite_status(w, tid, status, cwd):
    """Set the ticket's `status:` to anything, from anything.

    `set_status` asserts the document is at `awaiting-qa`; a run branch that already carries an
    earlier round's verdict is not, and criterion 2 needs both branches to name a status.
    """
    p = cwd / TKT_DIR / f"{tid}.md"
    lines = p.read_text().splitlines()
    at = next((i for i, line in enumerate(lines) if line.startswith("status:")), -1)
    assert at >= 0, f"{tid}.md has no status line:\n" + "\n".join(lines)
    lines[at] = f"status: {status}"
    p.write_text("\n".join(lines) + "\n")


def role_branch_only(w, tid, *, verdict="PASS WITH MINORS", status="pass", record=True,
                     note="", who=REVIEWER):
    """What a real QA lane leaves behind, and nothing else.

    The QA lane is a checkout cut from the run branch at claim time. It commits the verdict and the
    ticket's new `status:` on `ticket/<id>-qa` and pushes that branch by name. It cannot move
    `ticket/<id>`: that ref is checked out in the run lane's worktree, so `git branch -f` and
    `git fetch origin ticket/<id>:ticket/<id>` are both refused, and `S4` confines it to its own
    worktree so it cannot fast-forward the run lane's checkout either. Nothing merges the verdict
    back — which is the whole difference between this helper and `qa_branch`.
    """
    qa_wt = w.worktree(f"ticket/{tid}-qa", base=f"ticket/{tid}")
    w.write(f"{RPT_DIR}/{tid}-qa.md", verdict_text(tid, verdict) + note, cwd=qa_wt)
    rewrite_status(w, tid, status, qa_wt)
    w.git.commit(f"{tid}: QA verdict — {verdict}", who=who, cwd=qa_wt)
    tip = w.tip("HEAD", cwd=qa_wt)
    w.git("worktree", "remove", str(qa_wt))
    if record:
        plant_role_run(w, tid, "qa")
    return tip


def refs_the_tool_wrote(argvs, branch):
    """Every invocation the product logged that could move `branch`'s own ref.

    Swept off the product's own argv log rather than from a list anybody typed, so a new way of
    moving a ref is caught by the same assertion.
    """
    moved = []
    for argv in argvs:
        if "update-ref" in argv and any(branch in token for token in argv):
            moved.append(argv)
        if "branch" in argv:
            rest = argv[argv.index("branch") + 1:]
            if any(token == branch for token in rest) and rest[:1] != ["-D"]:
                moved.append(argv)
        if "push" in argv and any(branch in token for token in argv):
            moved.append(argv)
        if "merge" in argv and any(branch == token for token in argv):
            moved.append(argv)
    return moved


def scratch_worktrees(w):
    return [line[len("worktree "):]
            for line in w.git("worktree", "list", "--porcelain").splitlines()
            if line.startswith("worktree ") and "factory-land-" in line]


# ── criterion 1 ───────────────────────────────────────────────────────────────────────────────

def test_a_verdict_on_the_role_branch_alone_lands(world, serve):
    """The ordinary state after a QA round: the verdict is on the QA lane's branch and nowhere else.

    §1 criterion 1. The run branch is at `awaiting-qa` and carries no verdict document at all; the
    role branch descends from its tip and carries `PASS WITH MINORS` and `status: pass`; a role run
    record exists. One squash reaches `main`, its tree is the ROLE branch's tree byte for byte, and
    the only thing that touched the run branch's ref was the landing's reap.
    """
    w = world
    ticket_on_main(w, "T-RO")
    wt = work_branch(w, "T-RO")
    run_tip = w.tip("ticket/T-RO")
    qa_tip = role_branch_only(w, "T-RO")
    w.push_main()

    # the two sources must DISAGREE before anything is asserted, or this case passes whichever
    # branch the tool happened to read
    assert not w.git.ok("rev-parse", "--verify", "--quiet",
                        f"ticket/T-RO:{RPT_DIR}/T-RO-qa.md"), (
        "the run branch must carry NO verdict, which is what a QA lane always leaves behind")
    assert w.git.ok("rev-parse", "--verify", "--quiet",
                    f"ticket/T-RO-qa:{RPT_DIR}/T-RO-qa.md"), (
        "and the role branch must carry it")
    assert "status: awaiting-qa" in w.git("show", f"ticket/T-RO:{TKT_DIR}/T-RO.md"), (
        "the run branch's ticket must still say awaiting-qa")
    assert "status: pass" in w.git("show", f"ticket/T-RO-qa:{TKT_DIR}/T-RO.md"), (
        "and only the role branch's copy may say pass")
    assert w.git.ok("merge-base", "--is-ancestor", run_tip, qa_tip), (
        "the role branch descends from the run branch's tip")
    assert w.tip("ticket/T-RO") == run_tip, "and the run branch has not been moved by the fixture"

    before = w.tip("main")
    qa_tree = w.git("rev-parse", f"{qa_tip}^{{tree}}")

    srv = serve_fast(serve, w.root)
    wait_until("T-RO to land", lambda: w.tip("main") != before, timeout=90)
    wait_until("the remote to have it", lambda: w.remote_main() == w.tip("main"), timeout=90)
    settle(8)

    said = [x for x in land_lines(srv) if "T-RO" in x]
    assert w.git("rev-list", "--count", f"{before}..main") == "1", (
        "a verdict a QA lane wrote on its own branch lands, and it lands once:\n"
        + w.git("log", "--oneline", f"{before}..main") + "\n" + "\n".join(said))
    assert w.tree("main") == qa_tree, (
        "and the tree on main is the ROLE branch's tree byte for byte — the run branch's tree "
        "plus the verdict, which is what main should hold:\n" + "\n".join(said))
    assert (w.root / "src" / "T-RO.txt").exists(), "the work is on disk"
    assert (w.root / RPT_DIR / "T-RO-qa.md").exists(), "and so is the verdict"
    assert "status: pass" in (w.root / TKT_DIR / "T-RO.md").read_text(), (
        "and the ticket main carries says pass")
    subject = w.git("log", "-1", "--format=%s", "main")
    assert subject.startswith("T-RO: "), f"the subject carries the ticket id: {subject!r}"

    argvs = spawned(srv)
    assert argvs, "the product logged no argv at all, so this sweep has examined nothing"
    moved = refs_the_tool_wrote(argvs, "ticket/T-RO")
    assert not moved, (
        "nothing but the reap may touch the run branch's ref — the tool does not fix the "
        f"branch up for itself: {moved}")
    wait_until("the run branch to be reaped", lambda: not w.has_branch("ticket/T-RO"))
    assert not os.path.exists(str(wt)), "and its checkout goes with it"


# ── criterion 2 ───────────────────────────────────────────────────────────────────────────────

def test_the_descendant_decides_when_both_branches_carry_a_verdict(world, serve):
    """Two worlds that disagree, in both directions, decided by which tip contains the other.

    §1 criterion 2. (a) the run branch carries an older PASS and `status: pass`, the role branch
    descends from it with a later FAIL and `status: fail` — nothing lands and the refusal names the
    ROLE branch. (b) the run branch carries an older FAIL, the role branch descends with PASS and
    `status: pass` — it lands. A tool reading the run branch gets both of them exactly backwards,
    so neither can be green for the reason the other is.
    """
    w = world

    ticket_on_main(w, "T-D1")
    wt1 = work_branch(w, "T-D1")
    self_passed(w, "T-D1", wt1)
    d1_run = w.tip("ticket/T-D1")
    d1_role = role_branch_only(w, "T-D1", verdict="FAIL", status="fail")

    ticket_on_main(w, "T-D2")
    wt2 = work_branch(w, "T-D2")
    w.write(f"{RPT_DIR}/T-D2-qa.md", verdict_text("T-D2", "FAIL"), cwd=wt2)
    rewrite_status(w, "T-D2", "fail", wt2)
    w.git.commit("T-D2: QA verdict — round 1 FAIL", who=REVIEWER, cwd=wt2)
    d2_run = w.tip("ticket/T-D2")
    d2_role = role_branch_only(w, "T-D2", verdict="PASS WITH MINORS", status="pass")
    w.push_main()

    for tid, run_tip, role_tip, run_says, role_says in (
            ("T-D1", d1_run, d1_role, "pass", "fail"),
            ("T-D2", d2_run, d2_role, "fail", "pass")):
        assert w.git.ok("merge-base", "--is-ancestor", run_tip, role_tip), (
            f"{tid}'s role branch must descend from its run branch")
        assert run_tip != role_tip, f"{tid}'s two tips must differ"
        assert f"status: {run_says}" in w.git("show", f"{run_tip}:{TKT_DIR}/{tid}.md"), (
            f"{tid}'s run branch must say status: {run_says}")
        assert f"status: {role_says}" in w.git("show", f"{role_tip}:{TKT_DIR}/{tid}.md"), (
            f"{tid}'s role branch must say status: {role_says}, so the two DISAGREE")
        heads = (w.git("show", f"{run_tip}:{RPT_DIR}/{tid}-qa.md").splitlines()[0],
                 w.git("show", f"{role_tip}:{RPT_DIR}/{tid}-qa.md").splitlines()[0])
        assert heads[0] != heads[1], f"{tid}'s two verdict headings must disagree: {heads}"

    before = w.tip("main")
    srv = serve_fast(serve, w.root)
    wait_until("the tool to have judged both",
               lambda: all(any(t in line for line in land_lines(srv)) for t in ("T-D1", "T-D2")),
               timeout=90)
    wait_until("T-D2, whose live verdict passes, to land",
               lambda: (w.root / "src" / "T-D2.txt").exists(), timeout=90)
    settle(8)

    said = land_lines(srv)
    assert w.git("rev-list", "--count", f"{before}..main") == "1", (
        "exactly one of the two may land:\n" + w.git("log", "--oneline", f"{before}..main")
        + "\n" + "\n".join(said))
    assert w.tree("main") == w.git("rev-parse", f"{d2_role}^{{tree}}"), (
        "(b) what landed is the ROLE branch's tree, verdict and all")
    assert w.remote_main() == w.tip("main"), "and the remote has it"

    assert not (w.root / "src" / "T-D1.txt").exists(), (
        "(a) the later FAIL on the role branch decides, and an older PASS on the run branch does "
        "not overrule it:\n" + "\n".join(said))
    assert w.has_branch("ticket/T-D1"), "T-D1's branch survives"
    assert w.tip("ticket/T-D1") == d1_run, "with its tip where it was"
    refused = [x for x in said if "T-D1" in x]
    assert any("ticket/T-D1-qa" in x and "PASS" in x for x in refused), (
        f"and the refusal names the branch it was read from — the ROLE branch: {refused}")


# ── criterion 3 ───────────────────────────────────────────────────────────────────────────────

def test_diverged_run_and_role_branches_land_nothing_and_name_both_tips(world, serve):
    """MANDATORY NEGATIVE — two tips neither of which contains the other are a person's decision.

    §1 criterion 3, and the `WEB-FEAT-366` shape of 2026-09-08: the QA lane branched, wrote its
    verdict, and the run lane committed one report fix afterwards, so neither tip contains the
    other. Merging them has a conflict surface and `S7` says the tool edits no tree it did not
    create, so it lands nothing, moves nothing, leaves no scratch checkout behind, and names both
    tips so a person can settle it.
    """
    w = world
    ticket_on_main(w, "T-DIV")
    wt = work_branch(w, "T-DIV")
    qa_tip = role_branch_only(w, "T-DIV")
    w.write(f"{RPT_DIR}/T-DIV-report.md",
            "# T-DIV — report\n\nIt was built, and this line fixes a typo QA never saw.\n", cwd=wt)
    w.git.commit("T-DIV: fix a typo in the report", who=MEMBER, cwd=wt)
    run_tip = w.tip("ticket/T-DIV")
    w.push_main()

    assert not w.git.ok("merge-base", "--is-ancestor", run_tip, qa_tip), (
        "the run branch must hold a commit the role branch lacks")
    assert not w.git.ok("merge-base", "--is-ancestor", qa_tip, run_tip), (
        "and the role branch one the run branch lacks — that is what diverged means")
    assert w.git.ok("rev-parse", "--verify", "--quiet",
                    f"ticket/T-DIV-qa:{RPT_DIR}/T-DIV-qa.md"), (
        "and the role branch really carries the verdict, or this refuses for another reason")

    before = w.tip("main")
    tree_before = w.tree("main")
    remote_before = w.remote_main()
    head_before = w.git("rev-parse", "HEAD")
    checkout_before = checkout_status(w)

    srv = serve_fast(serve, w.root)
    lines = wait_until(
        "the tool to say the two branches have diverged",
        lambda: [x for x in land_lines(srv) if "T-DIV" in x and "diverged" in x], timeout=90)
    settle(8)

    said = [x for x in land_lines(srv) if "T-DIV" in x]
    assert w.tip("main") == before, (
        "nothing may land while the two tips disagree:\n"
        + w.git("log", "--oneline", f"{before}..main") + "\n" + "\n".join(said))
    assert w.tree("main") == tree_before, "and main's tree is exactly as it was"
    assert w.remote_main() == remote_before, "no remote ref moved"
    assert w.has_branch("ticket/T-DIV") and w.tip("ticket/T-DIV") == run_tip, (
        "the run branch survives with its tip where it was")
    assert w.has_branch("ticket/T-DIV-qa") and w.tip("ticket/T-DIV-qa") == qa_tip, (
        "and so does the role branch")
    assert os.path.exists(str(wt)), "the lane checkout is not reaped"
    assert w.git("rev-parse", "HEAD") == head_before, "the operator's checkout did not move"
    assert checkout_status(w) == checkout_before, "and is byte-identical"
    assert not (w.root / ".git" / "MERGE_HEAD").exists(), "with no merge left half-done in it"
    assert scratch_worktrees(w) == [], (
        "and no scratch checkout of the tool's own is left behind")

    named = [x for x in lines if run_tip in x and qa_tip in x]
    assert named, (
        "the log line must carry BOTH tips by sha, or nobody can settle it without going and "
        f"looking: {lines}")
    line = named[0]
    assert "ticket/T-DIV" in line and "ticket/T-DIV-qa" in line, (
        f"and both branches by name: {line}")
    assert "have diverged" in line and "merge one into the other" in line, (
        f"and say what to do about it: {line}")


# ── criterion 4 ───────────────────────────────────────────────────────────────────────────────

def test_a_role_branch_verdict_without_a_role_record_still_lands_nothing(world, serve):
    """The separate-session guard is not weakened by reading a second branch.

    §1 criterion 4. (a) the run branch passed itself and a branch wearing the role branch's own
    name descends from it carrying the verdict — no run record, so the tool refuses with
    `no role session recorded`, exactly as today. (b) criterion 1's world with the record taken
    away: the verdict lives only on `ticket/<id>-qa`, no session was ever dispatched, nothing
    lands. Refs are not evidence; the tool's own ledger is.
    """
    w = world

    ticket_on_main(w, "T-NR1")
    wt1 = work_branch(w, "T-NR1")
    self_passed(w, "T-NR1", wt1)
    nr1_run = w.tip("ticket/T-NR1")
    nr1_role = role_branch_only(w, "T-NR1", record=False,
                                note="Round 2: re-run after the fix, and it stands.\n")

    ticket_on_main(w, "T-NR2")
    work_branch(w, "T-NR2")
    nr2_run = w.tip("ticket/T-NR2")
    nr2_role = role_branch_only(w, "T-NR2", record=False)
    w.push_main()

    runs = w.root / ".factory" / "runs"
    assert not runs.exists() or not list(runs.iterdir()), (
        "no session may be recorded for either ticket, or this case tests nothing: "
        f"{sorted(p.name for p in runs.iterdir()) if runs.exists() else []}")
    for tid, run_tip, role_tip in (("T-NR1", nr1_run, nr1_role),
                                   ("T-NR2", nr2_run, nr2_role)):
        assert w.git.ok("merge-base", "--is-ancestor", run_tip, role_tip), (
            f"{tid}'s role branch descends from its run branch")
        assert w.git.ok("rev-parse", "--verify", "--quiet",
                        f"{role_tip}:{RPT_DIR}/{tid}-qa.md"), (
            f"and really carries {tid}'s verdict at its own tip")
        assert "PASS" in w.git("show", f"{role_tip}:{RPT_DIR}/{tid}-qa.md"), (
            "and that verdict says PASS, so only the missing record can be refusing it")
        assert "status: pass" in w.git("show", f"{role_tip}:{TKT_DIR}/{tid}.md"), (
            "and the ticket on it says pass")
    assert not w.git.ok("rev-parse", "--verify", "--quiet",
                        f"{nr2_run}:{RPT_DIR}/T-NR2-qa.md"), (
        "T-NR2 is criterion 1's world exactly, minus the record")

    before = w.tip("main")
    remote_before = w.remote_main()
    srv = serve_fast(serve, w.root)
    wait_until("the tool to have judged both",
               lambda: all(any(t in line for line in land_lines(srv))
                           for t in ("T-NR1", "T-NR2")), timeout=90)
    settle(8)

    said = land_lines(srv)
    assert w.tip("main") == before, (
        "no session other than the implementing one is recorded for either, so nothing may "
        "land:\n" + w.git("log", "--oneline", f"{before}..main") + "\n" + "\n".join(said))
    assert w.remote_main() == remote_before, "and no remote ref may move"
    for tid, run_tip, role_tip in (("T-NR1", nr1_run, nr1_role),
                                   ("T-NR2", nr2_run, nr2_role)):
        assert w.has_branch(f"ticket/{tid}") and w.tip(f"ticket/{tid}") == run_tip, (
            f"ticket/{tid} survives with its tip where it was")
        assert w.has_branch(f"ticket/{tid}-qa") and w.tip(f"ticket/{tid}-qa") == role_tip, (
            f"and so does ticket/{tid}-qa")
        assert not (w.root / "src" / f"{tid}.txt").exists(), f"{tid} reached nothing"
    assert any("T-NR1" in x and "no role session recorded" in x for x in said), (
        "and the refusal names what is actually missing — a session, not a ref: "
        + "\n".join(x for x in said if "T-NR1" in x))


# ── SRV-FIX-518 · a scratch outlives no attempt ───────────────────────────────────────────────
#
# `SRV-FEAT-437` criterion 3 asserted everything a conflicting landing may not damage — `main`, the
# remote, the branch, its worktree, `E5` — and the scratch worktree the tool makes for itself is
# named in none of it. `closeScratch_` removed it without `--force`, git refuses to remove a
# checkout holding conflict markers, and `openScratch_` refused a path that already existed. So the
# FIRST attempt named the conflict and every attempt after it on the same tip said
# `no scratch checkout could be made`, which names neither the conflict nor the paths. Four tickets
# sat behind that sentence for four and a half hours on 2026-09-11.
#
# The class is *a check about what an operation must not damage that never asks what the operation
# itself left behind*, and the second attempt is where it shows: no world in the suite had a second
# tick on a tip the first tick had already touched.


@pytest.fixture
def scratch_dir(tmp_path_factory):
    """Where the served server's `fs::temp_directory_path()` points, for this test alone.

    §1's safety clause: no case here may read, and certainly not delete, the `/tmp/factory-land-*`
    of the machine it runs on. `TMPDIR` is the first variable `std::filesystem::temp_directory_path`
    consults, so the product's own path derivation is untouched and only its root moves — and every
    case that uses this fixture asserts, off the product's own argv log, that the redirection
    actually took, because a temp directory that stayed behind would make every sweep below look
    clean for the wrong reason.
    """
    return tmp_path_factory.mktemp("landtmp")


def scratch_adds(srv, tid=None):
    """Every `git worktree add --detach <scratch>` the product logged, with the path it chose.

    Read off the product's own argv log rather than recomputed here: the path is
    `<temp>/factory-land-<id>-<purpose>-<pid>-<tip[0:12]>` and a test that rebuilt that string
    would be asserting its own arithmetic.
    """
    out = []
    for argv in spawned(srv):
        if "worktree" not in argv:
            continue
        at = argv.index("worktree")
        if argv[at + 1:at + 3] != ["add", "--detach"]:
            continue
        path = argv[at + 3]
        if tid is None or f"factory-land-{tid}-" in os.path.basename(path):
            out.append(path)
    return out


def with_what_it_said(srv, tid, what, predicate, timeout=90):
    """`wait_until`, whose failure carries what the tool said about this ticket instead.

    The defect's whole shape is a SENTENCE the operator gets in place of the one they need, so a
    timeout that does not quote the substitute is a red nobody can read — which is the state the
    operator was in for four and a half hours on 2026-09-11.
    """
    try:
        return wait_until(what, predicate, timeout=timeout)
    except AssertionError as why:
        said = [x for x in land_lines(srv) if tid in x]
        raise AssertionError(f"{why}\nwhat the tool said about {tid} instead:\n  "
                             + "\n  ".join(said or ["nothing at all"])) from None


def forced_removals(srv):
    """Every path the product asked git to remove BY FORCE, off its own argv log.

    `S7` is about what the tool reaches for, not only about what survives. A forced removal of a
    path git happens to refuse leaves the directory there and is still the tool trying to edit a
    working tree it did not create — and the next such directory is one git does not refuse.
    """
    out = []
    for argv in spawned(srv):
        if "worktree" not in argv:
            continue
        at = argv.index("worktree")
        if argv[at + 1:at + 3] == ["remove", "--force"] and len(argv) == at + 4:
            out.append(argv[at + 3])
    return out


def left_behind(scratch_dir, tid):
    """Every directory under the served temp directory this ticket's scratch naming could own."""
    return sorted(str(p) for p in scratch_dir.glob(f"factory-land-{tid}-*"))


def foreign_worktrees(w):
    """Every worktree registration that is neither the repository root nor one of its lanes.

    Derived from `git worktree list --porcelain`, so a registration of a shape nobody thought of
    is found by the same assertion — including a `prunable` one the tool left pointing at a
    directory that is gone.
    """
    lanes = str(w.root / ".factory" / "worktrees")
    out = []
    for line in w.git("worktree", "list", "--porcelain").splitlines():
        if not line.startswith("worktree "):
            continue
        at = line[len("worktree "):]
        if os.path.realpath(at) == os.path.realpath(str(w.root)):
            continue
        if os.path.realpath(at).startswith(os.path.realpath(lanes)):
            continue
        out.append(at)
    return out


def conflicting_world(w, tid):
    """`SRV-FEAT-437` criterion 3's world exactly: `main` moved onto the line the branch changed.

    Returns the tip of the branch the lander will read, which is the tip the scratch path is
    derived from.
    """
    ticket_on_main(w, tid)
    w.write("src/shared.txt", "one\ntwo\nthree\n")
    w.git.commit("the file both sides touch", who=MEMBER)
    w.push_main()

    wt = w.worktree(f"ticket/{tid}")
    w.write("src/shared.txt", "one\nBRANCH\nthree\n", cwd=wt)
    w.git.commit(f"{tid}: the branch's line", who=MEMBER, cwd=wt)
    w.write(f"{RPT_DIR}/{tid}-report.md", f"# {tid} — report\n", cwd=wt)
    w.git.commit(f"{tid}: the report", who=MEMBER, cwd=wt)
    qa_branch(w, tid, wt)

    w.write("src/shared.txt", "one\nMAIN\nthree\n")
    w.git.commit("somebody else's line, on main", who=MERGER)
    w.push_main()
    return wt


# ── criterion 1 ───────────────────────────────────────────────────────────────────────────────

def test_a_conflicting_merge_leaves_no_scratch_behind(world, serve, scratch_dir):
    """A landing attempt whose merge conflicts leaves nothing of its own behind.

    §1 criterion 1, on the same world `SRV-FEAT-437` criterion 3 uses. After the attempt has
    returned — `E5` naming the conflicting path is how the test knows it has — the repository's
    worktree list is the root and the lanes and nothing else, and the temp directory holds no
    `factory-land-<id>-*` directory at all.
    """
    w = world
    wt = conflicting_world(w, "T-518A")

    before = w.tip("main")
    srv = serve_fast(serve, w.root, TMPDIR=str(scratch_dir))
    d = wait_until("E5 to name the conflicting path",
                   lambda: (lambda x: x if "src/shared.txt" in x.get("detail", "") else None)(
                       delivery_of(srv, "T-518A")), timeout=90)
    settle(8)

    said = [x for x in land_lines(srv) if "T-518A" in x]
    assert d["needs_you"] is True, d

    # The sweep is only worth anything if the product really made a scratch, and really made it
    # HERE — a `TMPDIR` the product ignored would leave every assertion below trivially green.
    adds = scratch_adds(srv, "T-518A")
    assert adds, (
        "the product opened no scratch worktree for this ticket at all, so this case has examined "
        "nothing:\n" + "\n".join(said))
    for path in adds:
        assert path.startswith(str(scratch_dir) + os.sep), (
            f"the product put its scratch outside the temp directory this case owns, so the "
            f"sweep below is over the wrong directory: {path}")

    assert scratch_worktrees(w) == [], (
        "a conflicting attempt leaves no scratch worktree registered:\n"
        + w.git("worktree", "list", "--porcelain") + "\n" + "\n".join(said))
    assert foreign_worktrees(w) == [], (
        "and `git worktree list` names the root and the lanes and nothing else:\n"
        + w.git("worktree", "list", "--porcelain"))
    assert left_behind(scratch_dir, "T-518A") == [], (
        "and no directory of the tool's own is left under the temp directory: "
        + str(sorted(p.name for p in scratch_dir.iterdir())))

    # and the landing still refused for the reason it always did
    assert w.tip("main") == before, "main's tip must not move on a conflict"
    assert w.has_branch("ticket/T-518A"), "the branch survives a conflict"
    assert os.path.exists(str(wt)), "and so does its checkout"


# ── criterion 2 ───────────────────────────────────────────────────────────────────────────────

def test_the_second_attempt_on_a_conflicting_tip_is_a_fresh_attempt(world, serve, scratch_dir):
    """The second attempt on a conflicting tip names the conflict, exactly as the first did.

    §1 criterion 2. The lander memoises what it examined, so a tick with literally nothing changed
    is not an attempt at all — it re-examines when the base moves, which is what was happening on
    2026-09-11 while `FAC-EPIC-375`'s children landed one after another. So the fixture moves the
    ONE thing that provokes a second examination and touches nothing the conflict is about: a
    commit on `main` on an unrelated file. The branch's tip does not move, so the scratch path the
    product derives is byte-identical between the two attempts, and the case asserts that from the
    product's own argv log before it asserts anything else.

    A fixture that examines once is green against the code being replaced.
    """
    w = world
    conflicting_world(w, "T-518B")
    branch_tip = w.tip("ticket/T-518B")

    srv = serve_fast(serve, w.root, TMPDIR=str(scratch_dir))

    def conflicts_named():
        return [x for x in land_lines(srv)
                if "T-518B" in x and "will not merge into ticket/T-518B" in x]

    first = wait_until("the first attempt to name the conflict",
                       lambda: conflicts_named() or None, timeout=90)
    assert len(first) == 1, f"the fixture must produce ONE attempt before it moves main: {first}"

    # the second tick: `main` moves, and nothing the conflict is about changes
    w.write("src/somebody-elses-ticket.txt", "a different ticket landed while this one waited\n")
    w.git.commit("OTHER: somebody else's work, on main", who=MERGER)
    w.push_main()
    assert w.tip("ticket/T-518B") == branch_tip, (
        "the branch's own tip must not move, or the second attempt is on a different tip and the "
        "scratch path is a different path")
    assert w.git("show", "main:src/shared.txt") == "one\nMAIN\nthree", (
        "and the conflicting file must be exactly as it was on both sides")

    both = with_what_it_said(
        srv, "T-518B", "the second attempt to name the conflict",
        lambda: (lambda x: x if len(x) >= 2 else None)(conflicts_named()))
    settle(8)

    said = [x for x in land_lines(srv) if "T-518B" in x]

    # the two attempts are the same attempt, twice
    paths = [x.split("will not merge into ticket/T-518B", 1)[1] for x in both[:2]]
    assert paths[0] == paths[1], (
        f"both attempts must name the same conflicting paths, or the second is about something "
        f"else:\n  1: {both[0]}\n  2: {both[1]}")
    assert "src/shared.txt" in paths[0], (
        f"and the paths must be the file the two sides really disagree about: {both[0]}")

    adds = scratch_adds(srv, "T-518B")
    assert len(adds) >= 2, (
        f"the product must have opened a scratch for each attempt; it opened {len(adds)}:\n"
        + "\n".join(said))
    assert len(set(adds)) == 1, (
        f"and both attempts must have wanted the SAME path — that is what makes this the same "
        f"tip twice rather than two different worlds: {sorted(set(adds))}")

    # the sentence that was reaching the operator instead
    log = srv.log.read_text()
    assert "no scratch checkout" not in log, (
        "no attempt may report a missing scratch checkout — the first one's leftovers are what "
        "produced that sentence:\n" + "\n".join(said))
    d = delivery_of(srv, "T-518B")
    assert "no scratch checkout" not in d.get("detail", ""), d
    assert "will not merge into ticket/T-518B" in d.get("detail", ""), (
        f"and E5 carries the conflict, not the tool's own housekeeping: {d}")
    assert "src/shared.txt" in d.get("detail", ""), d

    assert scratch_worktrees(w) == [], "and nothing is left registered after either attempt"
    assert left_behind(scratch_dir, "T-518B") == [], (
        "nor on disk: " + str(sorted(p.name for p in scratch_dir.iterdir())))


# ── criterion 3 ───────────────────────────────────────────────────────────────────────────────

def test_a_foreign_directory_at_the_scratch_path_is_never_removed(world, serve, scratch_dir):
    """MANDATORY NEGATIVE (`S7`) — a working tree the tool did not create is left exactly alone.

    §1 criterion 3. *"The tool never edits a working tree it did not create."* Forcing the removal
    of a scratch is inside that seam because the scratch is the tool's own, three lines after it
    made it; a directory that merely stands at the path the tool would have chosen is not, whatever
    its name looks like. So the attempt is refused BY NAME, what is at the path is still there
    afterwards — read back, not stat'd — and nothing lands.

    The path is the squash scratch's, `factory-land-<id>-squash-<pid>-<base tip[0:12]>`: its two
    variables are the served process's pid and `main`'s tip, both of which this case knows before
    the ticket becomes landable, so each directory is planted with no race against the lander.

    **Two worlds, because criterion 3's own world cannot fail.** (a) is the criterion verbatim — a
    directory a person made, with one file in it. `git worktree remove --force` refuses a path that
    is not a registered worktree, so the file survives whether or not the tool checks first, and a
    case that only reads the file back is green against the code this ticket replaces. `WEB-FIX-031`
    is the same discovery: a negative whose subject the product refuses for another reason is not a
    negative. So (a) asserts at the boundary instead — the tool never ASKED for that path to be
    force-removed, swept off its own argv log — and (b) is the world git does not refuse: a
    registered worktree of this repository holding a branch and somebody's uncommitted work, which
    an unguarded `worktree remove --force` deletes outright.
    """
    w = world
    for tid in ("T-518C", "T-518D"):
        ticket_on_main(w, tid)
        work_branch(w, tid)
    w.push_main()

    before = w.tip("main")
    remote_before = w.remote_main()
    tips = {tid: w.tip(f"ticket/{tid}") for tid in ("T-518C", "T-518D")}

    srv = serve_fast(serve, w.root, TMPDIR=str(scratch_dir))
    wait_until("the tool to judge both tickets once, while they still carry no verdict",
               lambda: all(any(tid in x for x in land_lines(srv))
                           for tid in ("T-518C", "T-518D")), timeout=90)

    def squash_path(tid):
        return scratch_dir / f"factory-land-{tid}-squash-{srv.pid}-{before[:12]}"

    # (a) the criterion's own world: a directory a person made, and one file in it
    mine = squash_path("T-518C")
    mine.mkdir()
    (mine / "notes-of-my-own.md").write_text("a directory a person made, and what is in it\n")
    assert not w.git.ok("rev-parse", "--is-inside-work-tree", cwd=mine), (
        "the planted directory must not be a worktree of anything, or it is not the shape the "
        "criterion names")

    # (b) the world git does NOT refuse: a worktree of this repository, holding a branch and work
    theirs = squash_path("T-518D")
    w.git("worktree", "add", "-b", "somebody-elses/work", str(theirs), "main")
    (theirs / "src" / "half-written.txt").write_text("what I was in the middle of writing\n")
    assert w.git("status", "--porcelain", cwd=theirs) != "", (
        "the planted worktree must hold uncommitted work, or losing it is not losing anything")
    assert any(line == "branch refs/heads/somebody-elses/work"
               for line in w.git("worktree", "list", "--porcelain").splitlines()), (
        "and it must be a REGISTERED worktree holding a branch — the one shape "
        "`git worktree remove --force` carries out rather than refusing")

    roles = {tid: role_branch_only(w, tid) for tid in ("T-518C", "T-518D")}
    for tid in ("T-518C", "T-518D"):
        assert w.tip(f"ticket/{tid}") == tips[tid], (
            f"{tid}'s run branch must not move — the base tip is what names the squash scratch")

    for tid, planted in (("T-518C", mine), ("T-518D", theirs)):
        d = with_what_it_said(
            srv, tid, f"E5 to name the directory the tool would not touch, for {tid}",
            lambda tid=tid, planted=planted: (
                lambda x: x if str(planted) in x.get("detail", "") else None)(
                    delivery_of(srv, tid)))
        assert d["needs_you"] is True, d
        assert "no scratch checkout" not in d["detail"], d
    settle(8)

    said = land_lines(srv)

    # removes nothing, and never asks for it to be removed
    asked = forced_removals(srv)
    assert not [x for x in asked if x in (str(mine), str(theirs))], (
        "the tool asked git to force-remove a path it did not make. `S7` is about what the tool "
        f"reaches for, and git refusing one of these is not the tool refusing it: {asked}")
    assert mine.is_dir(), "(a) the directory must still be there:\n" + "\n".join(said)
    assert (mine / "notes-of-my-own.md").read_text() == (
        "a directory a person made, and what is in it\n"), (
        "(a) and the file in it must be byte-identical — a forced removal would have taken it")
    assert theirs.is_dir(), "(b) the worktree must still be there:\n" + "\n".join(said)
    assert (theirs / "src" / "half-written.txt").read_text() == (
        "what I was in the middle of writing\n"), (
        "(b) and the uncommitted work in it must be byte-identical — this is the world git does "
        "NOT refuse, so nothing but the tool's own check stands between it and `--force`")
    assert w.has_branch("somebody-elses/work"), "(b) and the branch it held survives"

    # and lands nothing
    assert w.tip("main") == before, (
        "nothing may land:\n" + w.git("log", "--oneline", f"{before}..main")
        + "\n" + "\n".join(said))
    assert w.remote_main() == remote_before, "and no remote ref moved"
    for tid in ("T-518C", "T-518D"):
        assert w.has_branch(f"ticket/{tid}"), f"ticket/{tid} survives"
        assert w.tip(f"ticket/{tid}") == tips[tid], f"with its tip where it was"
        assert w.has_branch(f"ticket/{tid}-qa") and w.tip(f"ticket/{tid}-qa") == roles[tid], (
            f"and so does ticket/{tid}-qa")
        assert not (w.root / "src" / f"{tid}.txt").exists(), f"and nothing of {tid} is on main"
    assert sorted(os.path.realpath(x) for x in foreign_worktrees(w)) == [
        os.path.realpath(str(theirs))], (
        "the tool registered nothing of its own, and the one foreign registration is the planted "
        "worktree:\n" + w.git("worktree", "list", "--porcelain"))


# ── SRV-FIX-532 cluster A2 — a landing is remembered by the tip it squashed ────────────────────
#
# `SRV-FIX-519`. Fifty seconds after landing `SRV-FEAT-498` as one squash, the lander examined it
# again, merged the new `main` — which now carries that very squash — into the branch it had just
# squashed, hit the conflict a squash guarantees, and left a conflicted scratch behind. The
# checkout had been KEPT because it holds ignored content, and `server/.cmake_build` is in every
# `factory-server` lane that ever built: so no server lane's checkout is ever reaped, every landed
# server branch is kept, and every kept branch was re-examined for ever.


def ignored_build_output(w, wt):
    """One ignored file in a ticket's checkout — the shape every `factory-server` lane has.

    `SRV-FEAT-437` criterion 5 keeps a checkout whose `status --ignored` is non-empty, and this is
    the smallest thing that makes it non-empty. The `.gitignore` is committed on `main` before the
    branch is cut so the branch inherits it, exactly as a real lane does.
    """
    ignore = w.root / ".gitignore"
    ignore.write_text(ignore.read_text() + "build/\n")
    w.git.commit("ignore build output", who=MEMBER)
    if wt is not None:
        (wt / "build").mkdir(parents=True, exist_ok=True)
        (wt / "build" / "one.o").write_text("object code\n")


def read_the_verdict(srv, tid):
    """Every `git cat-file blob <ref>:<the verdict document>` the product logged for this ticket.

    The marker for *this tick examined the ticket*. `attempt_` reads the verdict blob only after
    the landed-tip check has let it through, so a tick that stops at that check contributes
    nothing here and a tick that does not contributes one. Swept off the product's own argv log,
    never recomputed: the assertion is about what the tool RAN.
    """
    out = []
    for argv in spawned(srv):
        if "cat-file" not in argv:
            continue
        if any(f"{tid}-qa.md" in token for token in argv):
            out.append(argv)
    return out


def a_later_tick(srv, mark, timeout=30):
    """Wait until the product has spawned something after `mark`, and answer everything since.

    A tick that changes nothing still resolves the branch, the base and the remote, so the sweep
    is non-empty on every tick — which is what makes *"it attempted nothing"* an assertion about a
    tick that HAPPENED rather than about a poll that never came round.
    """
    wait_until(f"a further tick after {mark} git invocations",
               lambda: len(spawned(srv)) > mark, timeout=timeout)
    return spawned(srv)[mark:]


def test_a_landed_branch_that_was_kept_is_not_examined_again(world, serve, scratch_dir):
    """§1 A2 — a landed ticket whose checkout was kept is not a candidate until its tip moves.

    `SRV-FIX-519` criterion 1. The landing succeeds, the checkout and the branch are KEPT because
    the checkout holds one ignored file, and the log says why. Then the lander ticks again with
    nothing changed: it spawns git — so the tick really happened — and it reads no verdict, opens
    no scratch and runs no `merge`.

    RED before the fix: the second tick merges the new `main`, which now carries the squash, into
    the branch that was squashed, and leaves the conflict a squash guarantees.
    """
    w = world
    ticket_on_main(w, "T-532A")
    ignored_build_output(w, None)
    wt = work_branch(w, "T-532A")
    (wt / "build").mkdir(parents=True, exist_ok=True)
    (wt / "build" / "one.o").write_text("object code\n")
    qa_branch(w, "T-532A", wt)
    w.push_main()

    held = w.git("status", "--porcelain", "--ignored=matching", cwd=wt)
    assert any(line.startswith("!! ") for line in held.splitlines()), (
        "the fixture must leave ignored content in the checkout or the reap removes it and the "
        f"case is about nothing: {held!r}")

    before = w.tip("main")
    srv = serve_fast(serve, w.root, TMPDIR=str(scratch_dir))
    with_what_it_said(srv, "T-532A", "main to gain the landing",
                      lambda: w.tip("main") != before)
    squash = w.tip("main")

    with_what_it_said(
        srv, "T-532A", "the tool to say why it kept the checkout",
        lambda: [x for x in land_lines(srv) if "T-532A" in x and "ignored content" in x])
    assert w.has_branch("ticket/T-532A"), (
        "the branch must be KEPT — its checkout holds ignored content, which is the state every "
        "server lane is in and the state this case is about")
    assert os.path.exists(str(wt)), "the checkout must be kept too"

    settle(3)
    read_once = len(read_the_verdict(srv, "T-532A"))
    assert read_once >= 1, (
        "the tool never read the verdict off this branch, so the sweep this case is about is "
        "empty and it would pass having examined nothing")
    mark = len(spawned(srv))
    scratches = len(scratch_adds(srv, "T-532A"))

    # `watchDelivery` is onChange, not a timer, so a further look needs the world to have moved.
    # Somebody else filing a ticket is the smallest move there is, and it is the one that put
    # `SRV-FEAT-498` back in front of the lander fifty seconds after it landed.
    ticket_on_main(w, "T-532A-NEXT", title="somebody else's ticket")
    w.push_main()
    a_later_tick(srv, mark, timeout=60)
    settle(3)

    later = spawned(srv)[mark:]
    assert later, "no further tick happened, so this case proved nothing about the second one"
    assert len(read_the_verdict(srv, "T-532A")) == read_once, (
        "the landed branch was examined again — it read the verdict off it a second time:\n  "
        + "\n  ".join(str(x) for x in read_the_verdict(srv, "T-532A")))
    assert len(scratch_adds(srv, "T-532A")) == scratches, (
        f"a scratch checkout was opened for a branch that is already landed: "
        f"{scratch_adds(srv, 'T-532A')}")
    assert not [argv for argv in later if "merge" in argv], (
        "a landed branch was merged into again after it was landed — this is the conflict a "
        "squash guarantees, arriving by the second history:\n  "
        + "\n  ".join(str(x) for x in later if "merge" in x))
    assert not left_behind(scratch_dir, "T-532A"), (
        f"a scratch was left behind: {left_behind(scratch_dir, 'T-532A')}")

    d = with_what_it_said(srv, "T-532A", "E5 to say done",
                          lambda: (lambda x: x if x["state"] == "done" else None)(
                              delivery_of(srv, "T-532A")))
    assert d["needs_you"] is False, d
    landings = [x for x in w.git("log", "--format=%s", f"{before}..main").splitlines()
                if x.startswith("T-532A: ")]
    assert landings == [w.git("log", "-1", "--format=%s", squash)], (
        f"one ticket reached `main` more than once, so the landing was not idempotent: {landings}")


def test_a_kept_branch_that_moved_after_landing_is_examined_again(world, serve, scratch_dir):
    """§1 A2, the other direction — *until the branch's tip moves* is the whole condition.

    `SRV-FIX-519` criterion 2. The same world, then ONE new commit on the kept branch after the
    landing: a person kept working. The lander examines it again — it reads the verdict off the
    branch, which `attempt_` does only once the landed-tip check has let it through.

    This is the negative of the case above: a fix that remembers *this ticket landed* rather than
    *this TIP landed* passes that one and fails this one.
    """
    w = world
    ticket_on_main(w, "T-532B")
    ignored_build_output(w, None)
    wt = work_branch(w, "T-532B")
    (wt / "build").mkdir(parents=True, exist_ok=True)
    (wt / "build" / "one.o").write_text("object code\n")
    qa_branch(w, "T-532B", wt)
    w.push_main()

    before = w.tip("main")
    srv = serve_fast(serve, w.root, TMPDIR=str(scratch_dir))
    with_what_it_said(srv, "T-532B", "main to gain the landing",
                      lambda: w.tip("main") != before)
    assert w.has_branch("ticket/T-532B"), "the branch must be kept for this case to be about it"

    settle(3)
    read_once = len(read_the_verdict(srv, "T-532B"))
    assert read_once >= 1, (
        "the tool never read the verdict off this branch, so this case would pass having "
        "examined nothing")

    landed_tip = w.tip("ticket/T-532B")
    w.write("src/T-532B-more.txt", "a person kept working\n", cwd=wt)
    w.git.commit("T-532B: a person kept working after it landed", who=MEMBER, cwd=wt)
    assert w.tip("ticket/T-532B") != landed_tip, "the fixture did not move the tip"

    wait_until("the moved branch to be examined again",
               lambda: len(read_the_verdict(srv, "T-532B")) > read_once,
               timeout=90)


def test_a_hand_squashed_branch_is_not_mistaken_for_a_landed_one(world, serve, scratch_dir):
    """§1 A2 NEGATIVE — landed is the tool's own record, never a tree comparison.

    `SRV-FIX-519` criterion 3. A branch whose TREE equals a commit on `main` but which the tool
    never landed — a person squashed it by hand, and the subject on `main` carries no `<id>: `
    prefix — is examined like any other branch and disposed of by the existing rules.

    `server/design/land.md` already says the REAP may not be authorised by a tree comparison; this
    is the same sentence for the EXAMINATION, and it is what stops the fix for the two cases above
    being *"its tree is already there, so it is finished"*.
    """
    w = world
    ticket_on_main(w, "T-532C")
    wt = work_branch(w, "T-532C")
    qa_branch(w, "T-532C", wt)
    w.push_main()

    w.git("merge", "--squash", "ticket/T-532C")
    w.git("-c", f"user.name={MERGER[0]}", "-c", f"user.email={MERGER[1]}",
          "commit", "--no-gpg-sign", "-m", "a person landed this by hand, without the tool")
    w.push_main()
    assert w.tree("main") == w.tree("ticket/T-532C"), (
        "the fixture must leave main's tree equal to the branch's, or it cannot tell a tree "
        "comparison from a record")
    subject = w.git("log", "-1", "--format=%s", "main")
    assert not subject.startswith("T-532C"), (
        f"the hand squash must carry no ticket id, or the tool's own record could find it: "
        f"{subject!r}")

    srv = serve_fast(serve, w.root, TMPDIR=str(scratch_dir))
    wait_until("the hand-squashed branch to be examined",
               lambda: read_the_verdict(srv, "T-532C"), timeout=60)
    settle(5)
    assert not left_behind(scratch_dir, "T-532C"), (
        f"a scratch was left behind: {left_behind(scratch_dir, 'T-532C')}")


# ── SRV-FIX-532 cluster A3 — no ref a lane can write stands in for a recorded session ──────────
#
# `SRV-FIX-450`. `roleBranchWithVerdict_` accepted a role branch on either of two tests, and the
# second — `refHasVerdict_`, `git rev-parse --verify --quiet <ref>:<reports>/<id>-qa.md` — is the
# question *does a file exist at that path on that ref*. It says nothing about which verdict is
# there or who wrote it, so a role branch left behind by an earlier round validates a later verdict
# the implementing session wrote for itself.
#
# Topology cannot answer the question. A decoy `ticket/<id>-qa` the run lane creates at its own tip
# is indistinguishable, as a graph, from the legitimate fast-forward shape: in both the role branch
# IS the run branch's tip. So the answer is the tool's own record — `.factory/runs/`, written from
# the control plane, outside every lane's writable root — which now carries the branch each
# recorded session held and the tip that session left it at.


def session_left(w, tid, role, branch, tip, *, run_id=None, lane="lane/07"):
    """A run record for a session the TOOL dispatched, carrying the ref it left behind.

    `agent::Store::writeMeta_` writes `branch` and `branch_tip` on every meta it writes; this is
    the same bytes. `read_back_by_the_product` drives the product's own parser over them, so the
    fixture is never a stand-in for a record the product cannot read.
    """
    return plant_role_run(w, tid, role, run_id=run_id, lane=lane, branch=branch, tip=tip)


def refs_a_lane_can_write(w, tid, at):
    """Every ref shape a run lane is able to create, built here and swept by name.

    Derived from what the fixture CREATES rather than from a list typed into an assertion: the
    sweep below iterates this dictionary, so a shape added here is a shape the case drives.
    """
    made = {}
    w.git("branch", f"ticket/{tid}-qa", at)
    made["the role branch at the run tip"] = f"ticket/{tid}-qa"
    w.git("update-ref", f"refs/remotes/origin/ticket/{tid}-qa", at)
    made["a remote tracking ref"] = f"refs/remotes/origin/ticket/{tid}-qa"
    w.git("tag", f"ticket/{tid}-qa-tag", at)
    made["a tag"] = f"ticket/{tid}-qa-tag"
    w.git("branch", f"ticket/{tid}-qaa", at)
    made["a branch one character from the role branch"] = f"ticket/{tid}-qaa"
    return made


def test_a_stale_role_branch_does_not_validate_a_later_self_pass(world, serve):
    """§1 A3 — a ticket whose only PASS was written by the session that built it does not land.

    `SRV-FIX-450` criterion 1, and its probe 1 verbatim: `ticket/T-532D` carries the work; a REAL
    second session wrote a FAIL on `ticket/T-532D-qa` and that branch is kept, as `E13` keeps it;
    a `role: "qa"` run record exists. Then the run session commits its own `PASS WITH MINORS` and
    `status: pass` on the run branch. Nobody QA'd it again.

    RED before the fix: it lands on `main` and is pushed, because `refHasVerdict_` finds a file at
    that path on the role branch and asks nothing else.
    """
    w = world
    ticket_on_main(w, "T-532D")
    wt = work_branch(w, "T-532D")

    qa_wt = w.worktree("ticket/T-532D-qa", base="ticket/T-532D")
    w.write(f"{RPT_DIR}/T-532D-qa.md", verdict_text("T-532D", "FAIL"), cwd=qa_wt)
    w.git.commit("T-532D: QA verdict — FAIL", who=REVIEWER, cwd=qa_wt)
    role_tip = w.tip("HEAD", cwd=qa_wt)
    w.git("worktree", "remove", str(qa_wt))
    run_id = session_left(w, "T-532D", "qa", "ticket/T-532D-qa", role_tip)
    w.git("-c", f"user.name={MEMBER[0]}", "-c", f"user.email={MEMBER[1]}",
          "merge", "--no-ff", "--no-edit", "--no-gpg-sign", "ticket/T-532D-qa", cwd=wt)

    self_passed(w, "T-532D", wt)
    w.push_main()

    before = w.tip("main")
    remote_before = w.remote_main()
    srv = serve_fast(serve, w.root)
    read_back_by_the_product(srv, run_id)

    d = with_what_it_said(srv, "T-532D", "E5 to carry the lander's refusal",
                          lambda: (lambda x: x if "session" in x.get("detail", "") else None)(
                              delivery_of(srv, "T-532D")))
    settle(4)

    assert w.tip("main") == before, (
        "a verdict the implementing session wrote for itself reached `main`:\n"
        + w.git("log", "--oneline", f"{before}..main"))
    assert w.remote_main() == remote_before, "it was pushed, too"
    assert w.has_branch("ticket/T-532D"), "the branch must survive a refusal"
    assert "session" in d["detail"], (
        f"the refusal must name the session that is missing, not the ref: {d['detail']!r}")


def test_both_shapes_of_a_real_second_session_still_land(world, serve):
    """§1 A3 — a verdict a second session really wrote still lands, in both workflow shapes.

    `SRV-FIX-450` criterion 2. The role branch merged back with `--no-ff`, and the role branch
    fast-forwarded onto the run branch. Asserted on the same world as the case above, so a fix
    that passes that one by refusing everything fails here.
    """
    w = world
    for tid, shape in (("T-532E", "merged back with --no-ff"), ("T-532F", "fast-forwarded")):
        ticket_on_main(w, tid)
        wt = work_branch(w, tid)
        qa_wt = w.worktree(f"ticket/{tid}-qa", base=f"ticket/{tid}")
        w.write(f"{RPT_DIR}/{tid}-qa.md", verdict_text(tid), cwd=qa_wt)
        set_status(w, tid, "pass", qa_wt)
        w.git.commit(f"{tid}: QA verdict — PASS WITH MINORS", who=REVIEWER, cwd=qa_wt)
        role_tip = w.tip("HEAD", cwd=qa_wt)
        w.git("worktree", "remove", str(qa_wt))
        session_left(w, tid, "qa", f"ticket/{tid}-qa", role_tip)
        if shape.startswith("merged"):
            w.git("-c", f"user.name={MERGER[0]}", "-c", f"user.email={MERGER[1]}",
                  "merge", "--no-ff", "--no-edit", "--no-gpg-sign", f"ticket/{tid}-qa", cwd=wt)
        else:
            w.git("merge", "--ff-only", f"ticket/{tid}-qa", cwd=wt)
            assert w.tip(f"ticket/{tid}") == role_tip, "the fixture did not fast-forward"
    w.push_main()

    before = w.tip("main")
    srv = serve_fast(serve, w.root)
    for tid in ("T-532E", "T-532F"):
        with_what_it_said(srv, tid, f"{tid} to reach main",
                          lambda tid=tid: tid in w.git("log", "--format=%s", f"{before}..main"))


def test_no_ref_a_lane_can_write_stands_in_for_a_recorded_session(world, serve):
    """§1 A3 MANDATORY NEGATIVE — a branch the run lane can create never stands in for a session.

    `SRV-FIX-450` criterion 3. Over every ref a lane is able to write — the role branch at the run
    tip, a remote tracking ref, a tag, and a branch one character from the role branch's name —
    with a `role: "qa"` run record present in every case and NO record of the session having left
    that ref, nothing lands and the refusal names the session that is missing rather than the ref.

    The sweep is derived from the refs the fixture creates, never from a list in the assertion.
    """
    w = world
    ticket_on_main(w, "T-532G")
    wt = work_branch(w, "T-532G")
    self_passed(w, "T-532G", wt)
    run_id = plant_role_run(w, "T-532G", "qa", tip="")
    made = refs_a_lane_can_write(w, "T-532G", w.tip("ticket/T-532G"))
    assert len(made) >= 4, f"the sweep must not be empty or thin: {made}"
    w.push_main()

    before = w.tip("main")
    remote_before = w.remote_main()
    srv = serve_fast(serve, w.root)
    read_back_by_the_product(srv, run_id)

    d = with_what_it_said(srv, "T-532G", "E5 to carry the lander's refusal",
                          lambda: (lambda x: x if "session" in x.get("detail", "") else None)(
                              delivery_of(srv, "T-532G")))
    settle(4)

    for what, ref in sorted(made.items()):
        assert w.tip("main") == before, (
            f"{what} ({ref}) stood in for a session and the self-pass landed:\n"
            + w.git("log", "--oneline", f"{before}..main"))
        assert ref not in d["detail"], (
            f"the refusal names {what} rather than the session that is missing: {d['detail']!r}")
    assert w.remote_main() == remote_before, "it was pushed"
    assert "session" in d["detail"], (
        f"the refusal must name the missing session: {d['detail']!r}")


# ── SRV-FIX-532 cluster A4 — the squash author is the work's, not the verdict's ────────────────
#
# `SRV-FIX-477` and `SRV-FIX-425`. `squash_`'s tally iterates `git log --no-merges --format=%an`
# over `<base>..<tip>`, which git prints newest first, and replaces the incumbent only on a STRICT
# majority — so a tie goes to whoever committed last, and the QA session always commits last: it
# writes the verdict, by construction. `docs/operations/ESCAPES.md` 2026-09-04 carries the same cause twice
# already (`SRV-FIX-376`, `SRV-FIX-378`); this is the third round of it.


def landed_author(w, tid, before):
    """`(author, committer, body)` of the one commit `tid` put on `main`."""
    line = w.git("log", "--format=%H %s", f"{before}..main")
    shas = [x.split(" ", 1)[0] for x in line.splitlines() if x.split(" ", 1)[1:2]
            and x.split(" ", 1)[1].startswith(f"{tid}: ")]
    assert shas, f"{tid} put no commit on main:\n{line}"
    sha = shas[0]
    return (w.git("log", "-1", "--format=%an <%ae>", sha),
            w.git("log", "-1", "--format=%cn <%ce>", sha),
            w.git("log", "-1", "--format=%B", sha))


def test_a_tied_squash_author_is_the_member_not_the_last_committer(world, serve):
    """§1 A4 — a tie goes to the branch's own work, not to the last committer.

    `SRV-FIX-477` criterion 1. One member commit of work, one reviewer commit of verdict. The
    fixture asserts the two counts are EQUAL before it asserts anything about the product, because
    a fixture where they differ passes whichever rule the code has.

    RED before the fix: `Reviewer Three <reviewer@example.invalid>` is recorded as the author of
    the member's work, which is `docs/operations/ESCAPES.md`'s 2026-09-04 row for the third time.
    """
    w = world
    ticket_on_main(w, "T-532H")
    wt = w.worktree("ticket/T-532H")
    w.write("src/T-532H.txt", "T-532H did something\n", cwd=wt)
    w.git.commit("T-532H: the work and the report", who=MEMBER, cwd=wt,
                 when="2026-09-11T10:00:00+07:00")

    qa_wt = w.worktree("ticket/T-532H-qa", base="ticket/T-532H")
    w.write(f"{RPT_DIR}/T-532H-qa.md", verdict_text("T-532H"), cwd=qa_wt)
    set_status(w, "T-532H", "pass", qa_wt)
    w.git.commit("T-532H: QA verdict — PASS WITH MINORS", who=REVIEWER, cwd=qa_wt,
                 when="2026-09-11T11:00:00+07:00")
    role_tip = w.tip("HEAD", cwd=qa_wt)
    w.git("worktree", "remove", str(qa_wt))
    session_left(w, "T-532H", "qa", "ticket/T-532H-qa", role_tip)
    w.git("-c", f"user.name={MERGER[0]}", "-c", f"user.email={MERGER[1]}",
          "merge", "--no-ff", "--no-edit", "--no-gpg-sign", "ticket/T-532H-qa", cwd=wt)
    w.push_main()

    names = [x for x in w.git("log", "--no-merges", "--format=%an",
                              "main..ticket/T-532H").splitlines() if x]
    assert names.count(MEMBER[0]) == names.count(REVIEWER[0]) == 1, (
        f"the fixture must be a TIE or it cannot tell a tie-break from a rule: {names}")
    assert names[0] == REVIEWER[0], (
        f"the tie must be one the OLD rule loses — `git log` prints newest first and the loop "
        f"replaced the incumbent only on a strict majority, so the newest non-merge commit is "
        f"the author it named. This fixture's newest is {names[0]!r}: {names}")

    before = w.tip("main")
    srv = serve_fast(serve, w.root)
    with_what_it_said(srv, "T-532H", "main to gain the landing",
                      lambda: w.tip("main") != before)

    author, committer, body = landed_author(w, "T-532H", before)
    assert author == f"{MEMBER[0]} <{MEMBER[1]}>", (
        f"a tied tally must name the work's author, not whoever committed last: {author!r}")
    assert f"Co-Authored-By: {REVIEWER[0]} <{REVIEWER[1]}>" in body, (
        f"the reviewer is a trailer, not the author:\n{body}")
    assert "factory" not in committer or committer != author, committer


def test_a_majority_still_decides_the_squash_author(world, serve):
    """§1 A4 — a real majority still wins, in both directions.

    `SRV-FIX-477` criterion 2. Two members' work commits against one reviewer's work commit names
    the member; two reviewers' work commits against one member's names the reviewer. One case, two
    worlds, so the fix cannot be *"always prefer the oldest"* or *"always prefer the member"*.
    """
    w = world
    wanted = {}
    for tid, member_n, reviewer_n, expect in (("T-532I", 2, 1, MEMBER), ("T-532J", 1, 2, REVIEWER)):
        ticket_on_main(w, tid)
        wt = w.worktree(f"ticket/{tid}")
        for i in range(member_n):
            w.write(f"src/{tid}-m{i}.txt", f"{tid} member work {i}\n", cwd=wt)
            w.git.commit(f"{tid}: member work {i}", who=MEMBER, cwd=wt)
        for i in range(reviewer_n):
            w.write(f"src/{tid}-r{i}.txt", f"{tid} reviewer work {i}\n", cwd=wt)
            w.git.commit(f"{tid}: reviewer work {i}", who=REVIEWER, cwd=wt)
        qa_branch(w, tid, wt, merge_by=MERGER)
        wanted[tid] = expect
    w.push_main()

    before = w.tip("main")
    srv = serve_fast(serve, w.root)
    for tid in sorted(wanted):
        with_what_it_said(srv, tid, f"{tid} to reach main",
                          lambda tid=tid: f"{tid}: " in w.git("log", "--format=%s",
                                                              f"{before}..main"))
    for tid, expect in sorted(wanted.items()):
        author, _committer, _body = landed_author(w, tid, before)
        assert author == f"{expect[0]} <{expect[1]}>", (
            f"{tid}: the majority of the WORK commits is {expect[0]!r} and the landing says "
            f"{author!r}")


def test_no_landing_is_authored_by_the_tool(world, serve):
    """§1 A4 MANDATORY NEGATIVE — no landing is ever authored by the tool.

    `SRV-FIX-477` criterion 3. Across a world carrying three tickets of different compositions,
    every commit the tool put on `main` is committed by `factory` and authored by somebody who
    wrote at least one of that branch's own non-merge commits. Swept over the commits `main`
    actually gained, never over a list of tickets typed into the assertion.
    """
    w = world
    wrote = {}
    for tid, authors in (("T-532K", [MEMBER]),
                         ("T-532L", [MEMBER, MEMBER, MERGER]),
                         ("T-532M", [MERGER, REVIEWER])):
        ticket_on_main(w, tid)
        wt = w.worktree(f"ticket/{tid}")
        for i, who in enumerate(authors):
            w.write(f"src/{tid}-{i}.txt", f"{tid} work {i}\n", cwd=wt)
            w.git.commit(f"{tid}: work {i}", who=who, cwd=wt)
        qa_branch(w, tid, wt, merge_by=MERGER)
        wrote[tid] = {f"{a[0]} <{a[1]}>" for a in authors} | {f"{REVIEWER[0]} <{REVIEWER[1]}>"}
    w.push_main()

    before = w.tip("main")
    srv = serve_fast(serve, w.root)
    for tid in sorted(wrote):
        with_what_it_said(srv, tid, f"{tid} to reach main",
                          lambda tid=tid: f"{tid}: " in w.git("log", "--format=%s",
                                                              f"{before}..main"))
    settle(3)

    gained = [x for x in w.git("log", "--format=%H|%an <%ae>|%cn|%s",
                               f"{before}..main").splitlines() if x]
    assert len(gained) == len(wrote), (
        f"the sweep must see one commit per ticket and saw {len(gained)}:\n"
        + "\n".join(gained))
    for row in gained:
        _sha, author, committer, subject = row.split("|", 3)
        tid = subject.split(":", 1)[0]
        assert committer == "factory", (
            f"the tool lands on its own account and committed as {committer!r}: {row}")
        assert author != "factory <factory@localhost>", (
            f"a landing was authored by the tool: {row}")
        assert author in wrote[tid], (
            f"{tid} was landed under an author that wrote none of its non-merge commits: "
            f"{author!r} not in {sorted(wrote[tid])}")
