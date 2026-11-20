"""SRV-FIX-027 — which server a test reached, and how the fixture ends it.

Four sessions in a row ran green against a server they did not start: SRV-FEAT-001's first red
run "proved" a route was refused by a server that did not exist yet, SRV-FEAT-005 reached a
different checkout, SRV-FIX-020's QA re-ran with FACTORY_API overridden, WEB-FEAT-006 came back
`4 failed, 1 error`. `conftest.serve` has always asserted which repository the server it started
is serving; every other way of reaching a server skipped that assertion.

And every server the suite started was torn down with `proc.kill()`, so the shutdown path —
where SRV-FIX-025's race lived — was never taken by a single one of the 57 tests.

These cases judge the fixture, not the product. They run a CHILD pytest against a server that is
deliberately the wrong one, and assert that no path reaches it.
"""

import ast
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import requests

import conftest
from conftest import BINARY, SERVER_DIR


REPO_ROOT = Path(os.path.realpath(SERVER_DIR.parents[0]))   # SRV-FIX-495 — see conftest.py

# What the suite used to default to, and what four separate sessions found a stranger sitting on.
# `serve` will not hand out a fixed port — it only picks free ones — so the one test that needs
# this exact number opens it itself. That is the comment §1 asks for.
LEGACY_DEFAULT_PORT = 7777

MANIFEST = {
    "factory": "0.1",
    "workspaces": {"only": {"path": "."}},
    "docs": {"epics": "docs/epics", "tickets": "docs/tickets", "reports": "docs/reports"},
}


def _port_is_free(port):
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


class _Stranger:
    """A factory-server on a port of the test's choosing, serving a repository that is not this
    checkout — i.e. exactly what another session's leftover process looks like."""

    def __init__(self, root, port, log):
        self.root = Path(os.path.realpath(root))
        self.port = port
        self.base = f"http://127.0.0.1:{port}"
        self.log = log

    def tail(self):
        try:
            return self.log.read_text()
        except OSError:
            return "(no log)"

    def requests_served(self):
        """How many HTTP requests this process has answered, from its own access log."""
        return len(re.findall(r"\] (?:GET|POST) /", self.tail()))


@pytest.fixture
def stranger(tmp_path_factory):
    """Start a foreign server on a named port; stop it however this suite stops things."""
    started = []

    def _start(root, port):
        log = tmp_path_factory.mktemp("stranger") / "server.log"
        handle = log.open("w")
        env = dict(os.environ, FACTORY_HTTP_PORT=str(port))
        env.pop("FACTORY_API", None)
        proc = subprocess.Popen([str(BINARY), "--repo", str(root)],
                                env=env, stdout=handle, stderr=subprocess.STDOUT)
        started.append((proc, handle))
        inst = _Stranger(root, port, log)
        deadline = time.time() + 8
        while time.time() < deadline:
            assert proc.poll() is None, f"the foreign server exited {proc.returncode}\n{inst.tail()}"
            try:
                if requests.get(inst.base + "/api/health", timeout=1).status_code == 200:
                    return inst
            except requests.RequestException:
                time.sleep(0.05)
        raise AssertionError(f"foreign server never came up on {inst.base}\n{inst.tail()}")

    yield _start

    for proc, handle in started:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        handle.close()


def _child_pytest(*targets, **env):
    """Run part of this suite in its own process, with an environment we choose.

    The child is the thing under test: a session whose environment points at a stranger, which
    is what every one of the four incidents actually was.
    """
    e = dict(os.environ, **env)
    e.pop("PYTEST_CURRENT_TEST", None)
    return subprocess.run(
        [sys.executable, "-m", "pytest", *targets, "-q", "--tb=line", "-p", "no:cacheprovider"],
        cwd=str(SERVER_DIR), env=e, capture_output=True, text=True, timeout=600)


def _clients_of_the_shared_server(path):
    """Which cases in a file reach the server the ENVIRONMENT points at, read off the file.

    `FACTORY_API` decides where the `api` fixture, `api_base()`, the `ws` fixture and a bare
    `WsClient()` go; a case that starts its own server with `serve` goes nowhere near it and is
    not a client. SRV-FIX-532 §1 E2 put two such cases in `test_bootstrap.py`, and they pass
    against any environment because no environment is what they are about — so counting them as
    clients turns *nobody reached the stranger* into a failure about the wrong thing.

    Derived rather than listed: a list would have to be edited by whoever adds the next client,
    which is precisely the person who would not.
    """
    clients = []
    for node in ast.parse(Path(path).read_text()).body:
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
            continue
        fixtures = {a.arg for a in node.args.args}
        body = ast.dump(node)
        if fixtures & {"api", "ws"} or "api_base" in body or "WsClient" in body:
            clients.append(node.name)
    return clients


def _passed(blob):
    m = re.search(r"(\d+) passed", blob)
    return int(m.group(1)) if m else 0


def test_a_foreign_server_on_the_port_fails_the_suite(repo, serve, stranger):
    """Both halves of the incident: a stranger squatting the default port, and an environment
    aimed at one. Neither may produce a green run."""

    # ── half one: a foreign server on the port the suite used to default to ──────────────
    # It must not be reached AT ALL — not "reached and then checked". The proof is the
    # stranger's own access log: if the child talked to it, its log says so.
    squatter = None
    if _port_is_free(LEGACY_DEFAULT_PORT):
        squatter = stranger(repo.manifest(MANIFEST), LEGACY_DEFAULT_PORT)
    before = squatter.requests_served() if squatter is not None else 0

    child = _child_pytest("tests/test_bootstrap.py")
    blob = child.stdout + child.stderr

    if squatter is not None:
        # Counted, not searched: this fixture's own readiness probe is in that log too, and a
        # check that cannot tell it from the child's traffic is a check that never goes green.
        after = squatter.requests_served()
        assert after == before, (
            f"the suite made {after - before} request(s) to a server serving {squatter.root} "
            f"instead of {REPO_ROOT} — its log:\n{squatter.tail()[-1500:]}")
        assert child.returncode == 0, (
            f"the suite must start the server it means, not depend on one being there:\n"
            f"{blob[-3000:]}")

    # ── half two: an environment pointed at a stranger — SRV-FIX-020's QA did this by hand ──
    foreign = serve(repo.manifest(MANIFEST))
    out = _child_pytest("tests/test_bootstrap.py",
                        FACTORY_API=foreign.base,
                        FACTORY_HOST="127.0.0.1",
                        FACTORY_HTTP_PORT=str(foreign.port))
    blob = out.stdout + out.stderr
    assert out.returncode != 0, (
        f"the suite went green against a server serving {foreign.root}, "
        f"not {REPO_ROOT}:\n{blob[-3000:]}")
    assert str(foreign.root) in blob, (
        f"the failure never names the repository that answered ({foreign.root}):\n{blob[-3000:]}")
    assert str(REPO_ROOT) in blob, (
        f"the failure never names the repository that was expected ({REPO_ROOT}):\n{blob[-3000:]}")


def test_every_client_checks_identity(repo, serve):
    """Not one path in, all of them.

    `tests/test_bootstrap.py` reaches the server four different ways — the `api` fixture, the
    module-level base used directly by `test_engine_not_blocked`, the `ws` fixture, and a bare
    `WsClient()` in `test_second_socket_also_gets_hello`. Point the environment at a stranger
    and NONE of them may pass. A single green test in that run is a path that opted out.
    """
    clients = _clients_of_the_shared_server(SERVER_DIR / "tests" / "test_bootstrap.py")
    assert len(clients) >= 4, (
        f"the four ways in this docstring names are not in the file any more: {clients}")

    foreign = serve(repo.manifest(MANIFEST))
    out = _child_pytest(*[f"tests/test_bootstrap.py::{name}" for name in clients],
                        FACTORY_API=foreign.base,
                        FACTORY_HOST="127.0.0.1",
                        FACTORY_HTTP_PORT=str(foreign.port))
    blob = out.stdout + out.stderr
    assert _passed(blob) == 0, (
        f"{_passed(blob)} of {len(clients)} client(s) reached a server serving {foreign.root} "
        f"and passed:\n{blob[-3000:]}")
    assert str(foreign.root) in blob and str(REPO_ROOT) in blob, blob[-3000:]


def test_shutdown_runs_in_the_suite(repo, serve):
    """The fixture ends a server the way the product ends: SIGTERM, and it drains.

    SRV-FIX-025's race lived in a path no test had ever taken, because every fixture teardown
    was `proc.kill()`. This asserts the path is taken by an ordinary run.
    """
    stop = getattr(serve, "stop", None)
    assert stop is not None, (
        "conftest.serve has no shutdown path — every server in this suite is torn down with "
        "SIGKILL, so SRV-FIX-025's shutdown code is never executed by the suite at all")

    inst = serve(repo.manifest(MANIFEST))
    how, elapsed = stop(inst)

    assert how == "SIGTERM", f"the fixture ended the server with {how}, not a signal it handles"
    assert elapsed < conftest.DRAIN_BUDGET, f"drained in {elapsed:.2f}s, budget {conftest.DRAIN_BUDGET}s"
    log = inst.tail()
    assert "Graceful shutdown" in log, (
        f"the server never saw a signal it handles:\n{log[-1500:]}")
    assert "Shutting down" in log, (
        f"the server never reached forceStop_ — the path SRV-FIX-025's race lived in is still "
        f"untaken by the suite:\n{log[-1500:]}")
    assert inst.proc.returncode != -signal.SIGKILL, "it was killed after all"


def test_a_wedged_server_still_gets_killed(tmp_path):
    """MANDATORY NEGATIVE — this is the failure this ticket's change causes.

    Swapping kill for terminate is how a suite learns to hang, and a hung suite blocks every run
    while a stranger on a port only makes one run lie. A process that ignores SIGTERM must be
    SIGKILLed after the budget and the fixture must still return.
    """
    stop_process = getattr(conftest, "stop_process", None)
    assert stop_process is not None, (
        "conftest has no terminate-then-kill path, so nothing guards against a teardown that "
        "waits forever")

    wedged = tmp_path / "wedged.py"
    wedged.write_text(
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "print('wedged', flush=True)\n"
        "time.sleep(600)\n")
    proc = subprocess.Popen([sys.executable, str(wedged)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert proc.stdout.readline().strip() == "wedged"

    # On its own thread with a join deadline, because the failure being guarded against is a
    # teardown that never returns. A test that asserts "this returns" by calling it directly
    # does not fail when it doesn't — it hangs, which is the thing itself.
    out = {}

    def _stop():
        out["result"] = stop_process(proc, budget=1.0)

    t = threading.Thread(target=_stop, daemon=True)
    started = time.monotonic()
    t.start()
    t.join(timeout=6.0)
    wall = time.monotonic() - started
    try:
        assert not t.is_alive(), (
            f"the teardown has not returned after {wall:.1f}s for a 1.0s budget — every run of "
            "this suite now blocks on a process that ignores SIGTERM")
        how, elapsed = out["result"]
        assert how == "SIGKILL", f"a process ignoring SIGTERM was reported as {how}"
        assert proc.returncode == -signal.SIGKILL, proc.returncode
        assert wall < 6.0, f"the teardown took {wall:.1f}s for a 1.0s budget"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


# The historical numbers this project has squatted. 7777/7778 were the server's, 7779/7780 the
# client's dev and preview servers. A fixed port in a test or a dev entry point is an invitation
# to whatever else is on this machine — which is the whole of SRV-FIX-027.
FACTORY_PORT_RANGE = re.compile(r"\b77[0-9][0-9]\b")

PORT_FREE_FILES = [
    "server/tests/conftest.py",
    "client/web/vite.config.ts",
    "client/web/gate/run-gate.sh",
    "client/web/gate/browser-gate.mjs",
]


def test_no_entry_point_hardcodes_a_shared_port():
    """The structural half: nothing a developer or a test starts may name a fixed port.

    §1 pre-decided "ephemeral ports where possible". This is the assertion that keeps it true
    for the next file, instead of leaving it as a rule everyone has to remember — a rule every
    file has to remember is the same shape as the bug.

    Whole-line comments are exempt: writing down which port used to be squatted is how the next
    reader understands why none of these files names one. A line that is comment-and-code is not
    exempt — `http://localhost:7777` contains `//`, and a check clever enough to strip that is a
    check that stops looking at the exact line the bug lives on.
    """
    factory = SERVER_DIR.parent
    offenders = []
    for rel in PORT_FREE_FILES:
        path = factory / rel
        assert path.exists(), f"{rel} moved — this check silently stops covering it"
        for n, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("#"):
                continue
            if FACTORY_PORT_RANGE.search(line):
                offenders.append(f"{rel}:{n}: {stripped}")
    assert not offenders, (
        "a fixed port is named in code a developer or a test starts; two sessions on this "
        "machine will reach each other:\n  " + "\n  ".join(offenders))
