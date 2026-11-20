"""
Factory server — test fixtures.

The suite starts every server it talks to, on ports the OS hands out, and asserts which
repository each one is serving before a single test runs.

Run:                         cd factory/server && pytest tests/ -v

Nothing here names a port, and nothing assumes a server is already up. Both of those cost four
sessions before SRV-FIX-027: a stranger on the default port answers exactly like the server you
meant, and for endpoints that have not changed it answers correctly.

Set FACTORY_API to point the suite at a server you started yourself. That path is identity-checked
too — an override is a claim about which process you mean, not permission to skip the question.
"""

import atexit
import base64
import hashlib
import json
import os
import socket
import struct
import subprocess
import uuid
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import requests

SERVER_DIR = Path(__file__).resolve().parents[1]
_BINARY_OVERRIDE = os.environ.get("FACTORY_TEST_SERVER_BINARY")
if _BINARY_OVERRIDE is not None and (not _BINARY_OVERRIDE or not Path(_BINARY_OVERRIDE).is_absolute()):
    raise ValueError("FACTORY_TEST_SERVER_BINARY must name an absolute server binary path")
BINARY = (Path(_BINARY_OVERRIDE) if _BINARY_OVERRIDE is not None
          else SERVER_DIR.parent / ".cache" / "server" / "build" / "factory-server")

# The repository this checkout IS, and therefore the only one a server the suite talks to may be
# serving. `build.sh run` defaults to the same thing for the same reason.
#
# SRV-FIX-495: this said `parents[1]` — the directory ABOVE the checkout — while the sentence above
# it said what it says now. From a developer's checkout that is the monorepo, which has a manifest
# of its own, so every case worked and every case was about the repository this one is cloned INTO.
# From a lane it is `.factory/worktrees/lane`, which is no repository and holds no documents, and
# 41 cases failed for a reason that is about where they ran rather than about the tree.
REPO_ROOT = Path(os.path.realpath(SERVER_DIR.parents[0]))

# How long a server gets to drain after SIGTERM before it is killed. Generous: it is a deadline
# for a wedged process, not a performance budget — a healthy shutdown here takes milliseconds.
DRAIN_BUDGET = float(os.environ.get("FACTORY_DRAIN_BUDGET", "10"))


def check_identity(base, expected_root, timeout=5):
    """Which repository is the process answering `base` serving?

    This is the one question no test asked outside `serve`, four times running. It names BOTH
    roots because every one of those incidents was diagnosed by a human noticing `uptime_s` was
    too large — a failure that says only "wrong process" sends the next person to read a log.
    """
    r = requests.get(base + "/api/health", timeout=timeout)
    served = r.json().get("repo", {}).get("root")
    assert served == str(expected_root), (
        f"the server answering {base} is serving repository {served!r}, "
        f"but this suite is testing {str(expected_root)!r} — expected root {expected_root}, "
        f"answering root {served}. The test is talking to the wrong process."
    )
    return served


def stop_process(proc, budget=None, grace=None):
    """End a process the way the product is ended, and never wait forever.

    SIGTERM first, because a teardown that skips the shutdown path leaves the shutdown path with
    no gate at all — that is exactly how SRV-FIX-025's race lived to the first TSan run.

    Then SIGTERM again. `core::Engine` answers the first one by draining for a fixed ten seconds
    whether or not anything is connected, and cancels that drain on the second — the two-signal
    contract a developer uses as Ctrl-C, Ctrl-C. The second signal is not impatience: it is the
    only path to `forceStop_()`, which is where SRV-FIX-025's race actually was, and paying the
    full drain for every server in the suite would cost minutes.

    SIGKILL after the budget, because a suite that hangs blocks every run, which is worse than
    the bug this fixture fixes. Returns (how, seconds).
    """
    budget = DRAIN_BUDGET if budget is None else budget
    grace = min(0.15, budget / 2) if grace is None else grace
    if proc.poll() is not None:
        return "already-exited", 0.0
    started = time.monotonic()
    proc.terminate()
    try:
        proc.wait(timeout=grace)
        return "SIGTERM", time.monotonic() - started
    except subprocess.TimeoutExpired:
        pass
    proc.terminate()
    try:
        proc.wait(timeout=max(0.0, budget - (time.monotonic() - started)))
        return "SIGTERM", time.monotonic() - started
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
        return "SIGKILL", time.monotonic() - started


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Instance:
    """One factory-server process, pointed at one repository."""

    def __init__(self, base, root, log, pid=None, port=None, proc=None, handle=None):
        self.base = base
        self.root = root
        self.log = log
        self.pid = pid
        self.port = port
        self.proc = proc
        self.handle = handle
        self.host = urlsplit(base).hostname

    def get(self, path, **kw):
        return requests.get(self.base + path, timeout=5, **kw)

    def post(self, path, **kw):
        return requests.post(self.base + path, timeout=5, **kw)

    def put(self, path, **kw):
        return requests.put(self.base + path, timeout=5, **kw)

    def delete(self, path, **kw):
        return requests.delete(self.base + path, timeout=15, **kw)

    def ws(self, timeout=6):
        """A socket onto this instance, not onto whatever else is listening."""
        return WsClient(host=self.host, port=self.port, timeout=timeout)

    def tail(self):
        try:
            return self.log.read_text()[-2000:]
        except OSError:
            return "(no log)"


def _start_instance(root, log, **extra_env):
    """Start a server on a port nobody else holds and prove which repository it serves."""
    if _BINARY_OVERRIDE is not None:
        assert BINARY.is_file() and os.access(BINARY, os.X_OK), (
            f"FACTORY_TEST_SERVER_BINARY is missing or not executable: {BINARY}")
    else:
        assert BINARY.exists(), f"build first — cd factory/server && ./build.sh build ({BINARY})"
    http_port = _free_port()
    env = dict(os.environ, **extra_env)
    # A value of None means UNSET, not empty. `HOME` unset is what a systemd system unit gives a
    # process, and a guard that reads `getenv("HOME")` behaves differently there than under any
    # value a test could pass — so the suite has to be able to express the absence itself
    # (SRV-FIX-052, `test_the_home_guard_holds_when_the_server_has_no_home`).
    for key in [k for k, v in env.items() if v is None]:
        del env[key]
    env["FACTORY_HTTP_PORT"] = str(http_port)
    # SRV-FIX-062 — the mark that makes every helper under this server OURS, and everything
    # else on the machine not ours. `lane::git` execs the helper with `execvp`, so it inherits.
    env[SESSION_ENV] = SESSION_MARK
    env.pop("FACTORY_API", None)
    handle = log.open("w")
    proc = subprocess.Popen([str(BINARY), "--repo", str(root)],
                            env=env, stdout=handle, stderr=subprocess.STDOUT)
    inst = Instance(f"http://127.0.0.1:{http_port}", Path(os.path.realpath(root)), log,
                    pid=proc.pid, port=http_port, proc=proc, handle=handle)

    deadline = time.time() + 8
    while time.time() < deadline:
        if proc.poll() is not None:
            raise AssertionError(
                f"server exited {proc.returncode} before answering\n{inst.tail()}")
        try:
            r = requests.get(inst.base + "/api/health", timeout=1)
            if r.status_code == 200:
                check_identity(inst.base, inst.root)
                return inst
        except requests.RequestException:
            time.sleep(0.05)
    raise AssertionError(f"server never came up on {inst.base}\n{inst.tail()}")


# ── the one server the module-level clients talk to ─────────────────────────────────────────
# Started here rather than by a developer, so there is nothing for a stranger to answer instead.
# Built lazily: a suite that touches no HTTP client should not start a process.
_SESSION = None


def session_server():
    """The server this suite means — started by it, or named by FACTORY_API and checked."""
    global _SESSION
    if _SESSION is None:
        override = os.environ.get("FACTORY_API")
        if override:
            base = override.rstrip("/")
            u = urlsplit(base)
            inst = Instance(base, REPO_ROOT, Path(os.devnull),
                            port=u.port or (443 if u.scheme == "https" else 80))
            check_identity(base, REPO_ROOT)
            _SESSION = inst
        else:
            log = Path(tempfile.mkdtemp(prefix="factory-session-")) / "server.log"
            _SESSION = _start_instance(REPO_ROOT, log)
    return _SESSION


def api_base():
    """The base URL of the server this suite means. Never a guess, never a default port."""
    return session_server().base


def _stop_session():
    global _SESSION
    inst, _SESSION = _SESSION, None
    if inst is not None and inst.proc is not None:
        stop_process(inst.proc)
        if inst.handle is not None:
            inst.handle.close()


atexit.register(_stop_session)


def pytest_sessionfinish(session, exitstatus):
    _stop_session()


# ── nothing this suite starts outlives the test that started it (SRV-FIX-029) ───────────────
#
# A per-pid assertion is the wrong shape for a leak. `SRV-FEAT-008`'s forced release asserted
# `/proc/<killed_pid>` was gone — true, every time, while the sandboxed process it was supervising
# went on running: the pid the tool wrote down was the one process that was always going to die,
# and the survivor was the one it never had a number for. An assertion about a pid a supervisor
# already knows about cannot detect a process the supervisor has LOST TRACK OF, which is the only
# interesting kind (docs/operations/ESCAPES.md).
#
# So the check is not per test and not per pid: the process table is counted around EVERY test,
# and the test that increased the count is the test that failed. It therefore applies to tests
# that do not exist yet, which is the property the per-test assertion could never have.

# The confinement helper is the only thing this suite starts that can outlive it — `confine.cpp`
# execs it for every lane process, and `--unshare-pid` makes it PID 1 of the sandbox's namespace,
# so it is also the process whose survival keeps a whole sandbox alive.
CONFINE_HELPER = Path(os.environ.get("FACTORY_CONFINE_BIN", "bwrap")).name

# How long a helper on its way out has to finish going. Cheap when nothing leaked: the first look
# is taken immediately and a clean test never waits at all. Only a test that really left something
# behind pays this, and it pays it once.
LEAK_GRACE = float(os.environ.get("FACTORY_LEAK_GRACE", "3"))


# SRV-FIX-062 — OWNERSHIP IS ESTABLISHED, NEVER GUESSED FROM A STRING.
#
# The guard used to decide a helper was its own by matching this repository's path anywhere in the
# helper's command line. A lane belonging to a server the suite never started matches that exactly
# as well, so running the suite beside a live lane failed the suite with a leak that was not its
# own and `os.kill(pid, 9)`ed a person's agent mid-turn. Measured three times in one day: FEAT-040
# died at 5, 7 and 8 seconds with `turns: 0`; the same lane with nothing else running reached
# `completed`, 2 turns, $0.68.
#
# `SRV-FIX-038` closed this defect once already in `app/delivery/probe.cpp` — "a ticket id matches
# any process on the machine" — and the reasoning never reached this file, which did the same thing
# with a path.
#
# So the suite marks what it starts. Every server it launches carries `SESSION_ENV=SESSION_MARK` in
# its environment; `lane::git` execs the confinement helper with `execvp`, which inherits the
# environment, so every helper descended from one of this session's servers has the mark in
# `/proc/<pid>/environ` and nothing else on the machine does. The mark is a fresh uuid per pytest
# process, so two suites in sibling worktrees do not claim each other's either.
SESSION_ENV = "FACTORY_TEST_SESSION"
SESSION_MARK = f"pytest-{os.getpid()}-{uuid.uuid4().hex}"


def owned_confinement_helpers():
    """Every confinement helper descended from a server THIS pytest session started.

    Read off `/proc/<pid>/environ`, which is the only place the answer is a fact rather than a
    resemblance. A helper with no mark is somebody else's — a developer's lane, a sibling
    worktree's suite, a run a person is watching — and this suite has no business judging it and
    none at all killing it.
    """
    needle = f"{SESSION_ENV}={SESSION_MARK}".encode()
    found = {}
    with os.scandir("/proc") as entries:
        for entry in entries:
            if not entry.name.isdigit():
                continue
            try:
                # `comm` first: one short read per pid, and it rejects everything that is not a
                # helper before anything more expensive is opened.
                with open(f"/proc/{entry.name}/comm") as fh:
                    if fh.read().rstrip("\n") != CONFINE_HELPER:
                        continue
                with open(f"/proc/{entry.name}/environ", "rb") as fh:
                    if needle not in fh.read():
                        continue
                with open(f"/proc/{entry.name}/cmdline", "rb") as fh:
                    cmdline = fh.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
            except (OSError, ValueError):
                continue                      # it exited while being read: not a survivor
            found[int(entry.name)] = cmdline
    return found


@pytest.fixture(autouse=True)
def no_leaked_processes(request, tmp_path_factory):
    """FAIL any test that leaves a confinement helper running. Autouse: every test, always.

    Runs last of the function-scoped fixtures, after `serve` has stopped the servers a test
    started — so what it sees is what would still be there with nothing left to own it.
    """
    before = owned_confinement_helpers()
    yield
    deadline = time.monotonic() + LEAK_GRACE
    while True:
        leaked = {pid: cmd for pid, cmd in owned_confinement_helpers().items()
                  if pid not in before}
        if not leaked or time.monotonic() > deadline:
            break
        time.sleep(0.05)

    # Killed, and said so. A suite that reports a leak and leaves it running makes the next run's
    # baseline worse and eventually gets switched off; the evidence is in the message.
    for pid in leaked:
        try:
            os.kill(pid, 9)
        except OSError:
            pass

    assert not leaked, (
        f"{request.node.name} left {len(leaked)} confinement helper(s) running "
        f"{LEAK_GRACE}s after it finished, with nothing left supervising them "
        f"(they have been SIGKILLed now, so the next test starts clean):\n"
        + "\n".join(f"  pid {pid}: {cmd[:200]}" for pid, cmd in sorted(leaked.items()))
        + "\n  A process the tool has lost track of is the leak a per-pid assertion cannot see.")


@pytest.fixture
def api():
    return session_server()


@pytest.fixture
def repo(tmp_path):
    """A synthetic repository, built by the test.

    Never this one. A manifest test that only passes against the repository it was written in
    is a manifest test that has learned that repository.
    """

    class Repo:
        root = tmp_path
        real = Path(os.path.realpath(tmp_path))

        def manifest(self, content):
            d = tmp_path / ".factory"
            d.mkdir(exist_ok=True)
            text = content if isinstance(content, str) else json.dumps(content, indent=2)
            (d / "factory.json").write_text(text)
            return tmp_path

        def mkdir(self, rel):
            p = tmp_path / rel
            p.mkdir(parents=True, exist_ok=True)
            return p

        def write(self, rel, text):
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
            return p

    return Repo()


@pytest.fixture
def serve(tmp_path_factory):
    """Start a server pointed at a repository root, on ports chosen for this test alone.

    SRV-FEAT-001's first red run reached a server left over from the deleted prototype and
    "proved" behaviour of code that did not exist. Ephemeral ports make that impossible, and
    the identity check in `_start_instance` makes it visible if it ever becomes possible again.

    Teardown is the product's own shutdown, not SIGKILL: `serve.stop(inst)` is what the teardown
    calls, and a test may call it early to assert on how the server ended.
    """
    running = []

    def _serve(root, expect_start=True, **extra_env):
        log = tmp_path_factory.mktemp("srv") / "server.log"
        inst = _start_instance(root, log, **extra_env)
        running.append(inst)
        return inst

    def _launch_expecting_failure(argv):
        """Run the binary with the given argv and return (returncode, output)."""
        out = subprocess.run([str(BINARY)] + argv, capture_output=True, text=True, timeout=10,
                             env={k: v for k, v in os.environ.items()
                                  if k not in ("FACTORY_REPO",)})
        return out.returncode, out.stdout + out.stderr

    def _stop(inst, budget=None):
        """SIGTERM, then SIGKILL if it wedges. Returns (how, seconds)."""
        return stop_process(inst.proc, budget=budget)

    _serve.launch = _launch_expecting_failure
    _serve.stop = _stop
    yield _serve

    for inst in running:
        stop_process(inst.proc)
        if inst.handle is not None:
            inst.handle.close()


class WsClient:
    """A WebSocket client small enough to have no dependencies.

    The tests are the only thing proving the socket works, so they must not lean on a
    library that could hide a framing bug in the server.
    """

    def __init__(self, host=None, port=None, path="/ws", timeout=5):
        # No default host and no default port: a client built with neither gets the server this
        # suite started and checked, not whatever answers a number written down here.
        if host is None or port is None:
            inst = session_server()
            host = inst.host if host is None else host
            port = inst.port if port is None else port
        self.sock = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(
            f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\n"
            f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(1)
            if not chunk:
                raise AssertionError("server closed during the handshake")
            head += chunk
        assert b"101" in head.split(b"\r\n")[0], head.split(b"\r\n")[0]
        expect = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        assert expect.lower().encode() in head.lower(), "Sec-WebSocket-Accept is wrong"
        self.buf = b""

    def send(self, obj):
        """One masked text frame. Client frames MUST be masked; an unmasked one is a
        protocol error and Beast is right to hang up on it."""
        payload = (obj if isinstance(obj, (bytes, str)) else json.dumps(obj))
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        header = bytearray([0x81])
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < (1 << 16):
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def _read_exact(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise AssertionError("socket closed while reading a frame")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def recv(self):
        """One MESSAGE, decoded from JSON. Control frames are skipped.

        A message and a frame are not the same thing (RFC 6455 §5.4). Beast fragments any write
        larger than its write buffer — 4 KB by default — into a text frame with FIN clear followed
        by continuation frames, and a reader that returns the first one hands back a truncated
        JSON document. This read ignored FIN entirely until SRV-FIX-049, so **no case in this
        suite had ever read a socket message over about 4 KB**: they all failed with
        `JSONDecodeError: Unterminated string`, which reads like a broken server rather than a
        broken reader, so nobody wrote one. A lane snapshot of a real run is the first thing here
        big enough to cross that line, and it is exactly the frame this suite most needs to judge.
        """
        buf = b""
        started = None
        while True:
            b1, b2 = self._read_exact(2)
            fin = bool(b1 & 0x80)
            opcode = b1 & 0x0F
            length = b2 & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read_exact(8))[0]
            payload = self._read_exact(length) if length else b""
            if opcode == 0x8:                          # close
                raise AssertionError("server closed the socket")
            if opcode in (0x9, 0xA):                   # ping / pong — never fragmented
                continue
            if opcode == 0x0:                          # continuation
                assert started is not None, "a continuation frame with no message to continue"
            else:
                assert started is None, (
                    f"a new {opcode:#x} message began while {started:#x} was unfinished")
                started = opcode
            buf += payload
            if not fin:
                continue
            return json.loads(buf.decode("utf-8"))

    def wait_for(self, name, timeout=8):
        """The next frame named `name`, or an assertion saying what did arrive instead."""
        deadline = time.time() + timeout
        seen = []
        while True:
            left = deadline - time.time()
            if left <= 0:
                raise AssertionError(f"no {name!r} frame within {timeout}s; saw {seen}")
            self.sock.settimeout(left)
            try:
                frame = self.recv()
            except (socket.timeout, OSError) as exc:
                raise AssertionError(
                    f"no {name!r} frame within {timeout}s; saw {seen}") from exc
            if frame.get("m") == name or frame.get("type") == name:
                return frame
            seen.append(frame.get("m") or frame.get("type") or frame)

    def drain(self, seconds=0.6):
        """Every frame that arrives within `seconds`, then stop waiting.

        The assertion "and no frames for any other scope" needs this: proving something did
        NOT arrive means waiting long enough that it would have.
        """
        out = []
        deadline = time.time() + seconds
        while True:
            left = deadline - time.time()
            if left <= 0:
                return out
            self.sock.settimeout(left)
            try:
                out.append(self.recv())
            except (socket.timeout, OSError):
                return out

    def closed_by_server(self, timeout=8):
        """True when the server hangs up — a close frame, EOF or a reset all count."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.sock.settimeout(max(0.05, deadline - time.time()))
            try:
                self.recv()
            except AssertionError as exc:
                if "closed" in str(exc):
                    return True
                raise
            except socket.timeout:
                continue
            except OSError:
                return True
        return False

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


@pytest.fixture
def ws():
    c = WsClient()
    yield c
    c.close()
