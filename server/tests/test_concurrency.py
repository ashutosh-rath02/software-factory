"""SRV-FIX-024 — the engine is a pool, so shared state belongs on a strand.

`core::Engine` runs FOUR threads on one `io_context` (`ENGINE_THREADS`, default 4) and
`core::Worker` runs two more on its own. An asio handler is therefore not serialised by
default, and every document in this repository said "the engine thread", singular.

These tests make the thread count an assertion instead of a sentence, and run the catalog
under ThreadSanitizer while requests overlap and the file watch fires.

The TSan runs need a binary built with `-fsanitize=thread`. It is built into
`.cache/server/build-tsan/` on first use and cached; nothing in `CMakeLists.txt` changes for it.
"""

import json
import os
import re
import signal
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import requests

from conftest import SESSION_ENV, SESSION_MARK, WsClient

SERVER_DIR = Path(__file__).resolve().parents[1]
APP_DIR = SERVER_DIR / "app"
CORE_DIR = SERVER_DIR / "core"
TSAN_BUILD = SERVER_DIR.parent / ".cache" / "server" / "build-tsan"
TSAN_BINARY = TSAN_BUILD / "factory-server"
RELEASE_BINARY = SERVER_DIR.parent / ".cache" / "server" / "build" / "factory-server"
REPORT_DIR = SERVER_DIR.parents[0] / "docs" / "reports" / "SRV-FIX-024"

RACE_HEADER = "WARNING: ThreadSanitizer: data race"


# ── the pool ─────────────────────────────────────────────────────────────────────────────

def _threads_of(pid):
    return len(os.listdir(f"/proc/{pid}/task"))


def test_the_thread_count_is_stated_not_assumed(repo, serve):
    """The engine pool size is four, and ENGINE_THREADS moves it one thread for one.

    Counted from outside the process, because nothing inside it may be edited to say so:
    `core/` is not this ticket's to touch. A change to the default turns this red, which is
    the whole point — the number stops being folklore.
    """
    root = repo.manifest({"factory": "0.1", "workspaces": {"server": {"path": "."}}})

    counts = {}
    for label, env in (("default", {}), ("one", {"ENGINE_THREADS": "1"}),
                       ("four", {"ENGINE_THREADS": "4"}), ("nine", {"ENGINE_THREADS": "9"})):
        inst = serve(root, **env)
        time.sleep(0.4)                       # component start() runs after the pool spawns
        counts[label] = _threads_of(inst.pid)

    assert counts["nine"] - counts["one"] == 8, (
        f"ENGINE_THREADS does not map one env value to one thread: {counts}")
    assert counts["default"] == counts["four"], (
        f"the default engine pool is not 4 threads: {counts}")
    assert counts["default"] > counts["one"], (
        f"the server does not run on one engine thread; every document said it did: {counts}")


# ── the standing rule this ticket must not spend ─────────────────────────────────────────

def test_no_mutex_was_added():
    """asio's answer to handlers that share state is an executor, not a lock."""
    banned = re.compile(r"\b(std::mutex|std::recursive_mutex|std::shared_mutex"
                        r"|lock_guard|unique_lock|scoped_lock)\b")
    offenders = []
    for path in sorted(APP_DIR.rglob("*")):
        if path.suffix not in (".h", ".cpp"):
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if banned.search(line):
                offenders.append(f"{path.relative_to(SERVER_DIR)}:{n}: {line.strip()}")
    assert not offenders, "a mutex appeared in event-driven code:\n" + "\n".join(offenders)


# ── ThreadSanitizer ──────────────────────────────────────────────────────────────────────

def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="session")
def tsan_binary():
    """The server built with -fsanitize=thread. Cached across runs; no CMakeLists change."""
    subprocess.run(
        ["cmake", "-S", str(SERVER_DIR), "-B", str(TSAN_BUILD),
         "-DCMAKE_BUILD_TYPE=RelWithDebInfo",
         "-DCMAKE_CXX_FLAGS=-fsanitize=thread -g -O1",
         "-DCMAKE_EXE_LINKER_FLAGS=-fsanitize=thread"],
        check=True, capture_output=True, timeout=300)
    subprocess.run(["cmake", "--build", str(TSAN_BUILD), "-j", str(os.cpu_count() or 4)],
                   check=True, capture_output=True, timeout=1800)
    assert TSAN_BINARY.exists(), TSAN_BINARY
    return TSAN_BINARY


def _repo(root, documents=24):
    """A synthetic repository. Never this one — a test that only passes against the
    repository it was written in has learned that repository."""
    (root / ".factory").mkdir(parents=True, exist_ok=True)
    (root / ".factory" / "factory.json").write_text(json.dumps({
        "factory": "0.1",
        "workspaces": {"server": {"path": "."}},
        "docs": {"epics": "docs/epics", "tickets": "docs/tickets",
                 "reports": "docs/reports"},
    }))
    for name, kind in (("epics", "EPIC"), ("tickets", "TCK"), ("reports", "REP")):
        d = root / "docs" / name
        d.mkdir(parents=True, exist_ok=True)
        for i in range(documents):
            stem = f"{kind}-{i:03d}" + ("-report" if kind == "REP" else "")
            (d / f"{stem}.md").write_text(
                f"---\nid: {kind}-{i:03d}\ntitle: document {i}\nstatus: todo\n---\n\nbody {i}\n")
    return root


class TsanServer:
    def __init__(self, binary, root, log, **extra_env):
        self.root = root
        self.log = log
        self.port = _free_port()
        env = dict(os.environ,
                   FACTORY_HTTP_PORT=str(self.port),
                   TSAN_OPTIONS="halt_on_error=0 history_size=7 exitcode=0",
                   **extra_env)
        env[SESSION_ENV] = SESSION_MARK
        self.handle = log.open("w")
        # `setarch -R`: TSan's shadow mapping and this kernel's 32-bit mmap randomisation
        # disagree ("unexpected memory mapping"), and the disagreement is a coin flip per
        # start. Without this the run is not reproducible, and a flaky race hunt is not one.
        self.proc = subprocess.Popen(["setarch", "-R", str(binary), "--repo", str(root)],
                                     env=env, stdout=self.handle, stderr=subprocess.STDOUT)
        self.base = f"http://127.0.0.1:{self.port}"
        deadline = time.time() + 60           # a TSan build is an order of magnitude slower
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError(f"server exited {self.proc.returncode}\n{self.text()}")
            try:
                if requests.get(self.base + "/api/health", timeout=2).status_code == 200:
                    return
            except requests.RequestException:
                time.sleep(0.1)
        raise AssertionError(f"server never came up\n{self.text()}")

    def text(self):
        try:
            self.handle.flush()
        except (ValueError, OSError):
            pass
        try:
            return self.log.read_text(errors="replace")
        except OSError:
            return ""

    def shutdown(self):
        """The real shutdown path: SIGTERM drains, a second SIGTERM forces. `kill -9` would
        skip every component's stop(), which is half of what these runs are watching."""
        try:
            self.proc.send_signal(signal.SIGTERM)
            time.sleep(0.3)
            self.proc.send_signal(signal.SIGTERM)
            self.proc.wait(timeout=30)
        except (subprocess.TimeoutExpired, ProcessLookupError, OSError):
            self.proc.kill()
            self.proc.wait(timeout=10)
        finally:
            try:
                self.handle.close()
            except (ValueError, OSError):
                pass
        return self.text()


def _races(log_text):
    """Every data race TSan reported, as whole blocks, so a failure names the state."""
    out = []
    block = None
    for line in log_text.splitlines():
        if RACE_HEADER in line:
            block = [line]
            continue
        if block is not None:
            block.append(line)
            if line.startswith("=================="):
                out.append("\n".join(block))
                block = None
    if block:
        out.append("\n".join(block))
    return out


def _hammer(server, seconds, workers=12):
    """Overlapping reads of E3/E4/E5/E6 for `seconds`. Concurrency is the thing no gate in
    this repository had ever created."""
    paths = ["/api/requirements", "/api/requirements/EPIC-001", "/api/tickets/TCK-001",
             "/api/tickets/TCK-002/report", "/api/requirements?q=document"]
    deadline = time.time() + seconds
    errors = []

    def loop(i):
        session = requests.Session()
        while time.time() < deadline:
            try:
                session.get(server.base + paths[i % len(paths)], timeout=20)
            except requests.RequestException as exc:
                errors.append(str(exc))
                return

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(loop, range(workers)))
    return errors


def _churn(root, seconds, period=0.05):
    """A document changing on disk while those reads are in flight — the file watch's own
    input, and the only other writer of the catalog's state."""
    deadline = time.time() + seconds
    tickets = root / "docs" / "tickets"
    n = 0
    while time.time() < deadline:
        n += 1
        (tickets / "TCK-001.md").write_text(
            f"---\nid: TCK-001\ntitle: edit {n}\nstatus: todo\n---\n\nbody {n}\n")
        churned = tickets / f"TCK-9{n % 5:02d}.md"
        churned.write_text(f"---\nid: TCK-9{n % 5:02d}\ntitle: new {n}\n---\n\nnew\n")
        time.sleep(period)
        try:
            churned.unlink()
        except OSError:
            pass
        time.sleep(period)
    return n


def _app_races(log_text):
    """Races with a frame in `app/`, and the scope this check covers.

    The scope is still named rather than implied — `SRV-FIX-023`'s lesson, and these two
    cases genuinely only judge `app/`. What has changed is what is left over: SRV-FIX-024
    could not reach "no race at all" because `core` closed the acceptor from under its own
    accept handler, and recorded the core count instead. `SRV-FIX-025` fixed that and asserts
    the unweakened criterion in `test_no_core_race_at_shutdown`, so the count reported below
    is a signal that something new appeared, not a known exception being tolerated.
    """
    races = _races(log_text)
    app = [r for r in races if "/factory/server/app/" in r]
    return app, [r for r in races if r not in app]


def test_concurrent_catalog_reads_do_not_race(tsan_binary, tmp_path):
    """N overlapping requests against E3/E5 while a document changes on disk, under TSan."""
    root = _repo(tmp_path / "repo")
    server = TsanServer(tsan_binary, root, tmp_path / "tsan-reads.log")
    with ThreadPoolExecutor(max_workers=1) as churner:
        churner.submit(_churn, root, 6.0)
        errors = _hammer(server, 6.0)
    log = server.shutdown()

    app, core = _app_races(log)
    assert not app, (f"{len(app)} data race(s) in app/ under concurrent reads "
                     f"(+{len(core)} in core/, which SRV-FIX-025 should have left at zero):\n\n"
                     + "\n\n".join(app[:2]))
    assert not errors, f"requests failed while the watch was running: {errors[:3]}"
    # The run has to have actually exercised the thing: a server that answered nothing, or a
    # manifest that never loaded, produces a clean log for the wrong reason.
    assert "catalog reading" in log, f"the watch never started:\n{log[:1500]}"


def test_the_watch_and_a_read_do_not_race(tsan_binary, tmp_path):
    """The watch firing during an in-flight read is the interleaving `stamps_` and the
    change list are exposed to, and it is the one the singular-thread sentence excused."""
    root = _repo(tmp_path / "repo", documents=40)
    server = TsanServer(tsan_binary, root, tmp_path / "tsan-watch.log")
    with ThreadPoolExecutor(max_workers=1) as churner:
        churner.submit(_churn, root, 8.0, 0.02)
        _hammer(server, 8.0, workers=8)
    log = server.shutdown()

    app, core = _app_races(log)
    assert not app, (f"{len(app)} data race(s) in app/ while the watch ran "
                     f"(+{len(core)} in core/, which SRV-FIX-025 should have left at zero):\n\n"
                     + "\n\n".join(app[:2]))
    assert "catalog reading" in log, f"the watch never started:\n{log[:1500]}"
    assert "Shutting down" in log, (
        "the process was killed rather than stopped; stop() is half of what this run watches")


# ── the mandatory negative ───────────────────────────────────────────────────────────────

def test_the_race_is_demonstrated_before_it_is_fixed():
    """A synchronisation fix nobody watched fail is a fix nobody can show was needed.

    The two tests above are green once the strand is in, and a green test proves nothing
    about a guard that was never watched failing. So the pre-fix TSan report is committed as
    evidence and judged here: it has to be a real race report, from this server, naming the
    state this ticket moved onto a strand — not a sentence in a report claiming one happened.
    """
    assert REPORT_DIR.is_dir(), (
        f"no evidence directory at {REPORT_DIR}; the red run was not kept")
    reds = sorted(REPORT_DIR.glob("*tsan-red*.log"))
    assert reds, f"no *tsan-red*.log in {REPORT_DIR}"

    named = False
    for red in reds:
        text = red.read_text(errors="replace")
        races = _races(text)
        assert races, f"{red.name} contains no ThreadSanitizer race report"
        joined = "\n".join(races)
        assert re.search(r"Previous (read|write|atomic)", joined), (
            f"{red.name} shows only one access; a race needs the conflicting pair")
        if "catalog::Store::stop" in joined and "app/catalog/store.cpp" in joined:
            named = True
    assert named, ("the kept red run does not show catalog::Store::stop() against the watch — "
                   "that pair is the race this ticket guards")

    # The recipe, because a race whose natural window is microseconds is not reproducible by
    # asking nicely. It was made observable by holding the timer handler open across its read
    # of `stopping_`; that patch changes timing and no synchronisation, and re-applying it to
    # the fixed tree produces no race. Without the patch beside the log, "we saw it once" is
    # the whole of the evidence, and the next reader cannot check it.
    recipes = sorted(REPORT_DIR.glob("*race-window*.patch"))
    assert recipes, (f"no *race-window*.patch in {REPORT_DIR}; the red run cannot be "
                     "reproduced and so cannot be checked")
    recipe = recipes[0].read_text()
    assert "sleep_for" in recipe and "store.cpp" in recipe, (
        f"{recipes[0].name} does not widen the window in the file under test")


# ── SRV-FIX-025 — shutdown is behaviour, and core states its threading model ──────────────
#
# `SRV-FIX-024` could not make "no race report" true: `core::Engine::forceStop_()` calls every
# component's `stop()` on whichever engine thread took the signal, and `http::Server::stop()`
# closed the acceptor there and then while the accept handler read `is_open()` on another
# thread of the same pool. These four cases are that ticket's criterion, restored.

ENGINE_H = CORE_DIR / "engine.h"
ENGINE_CPP = CORE_DIR / "engine.cpp"

# core::Engine::drain_() gives a first SIGTERM ten seconds before it forces. A second signal
# skips the wait. Both numbers are read from the source below rather than believed.
DRAIN_SECONDS = 10.0
FORCE_BUDGET = 6.0
DRAIN_BUDGET = DRAIN_SECONDS + 8.0

# A one-in-three race is not observed by shutting down once. Twelve puts the chance of missing
# it below 1%, and the count is reported by every failure so a run that proved little says so.
SHUTDOWN_ITERATIONS = int(os.environ.get("FACTORY_SHUTDOWN_ITERATIONS", "12"))


def _accept_load(base, stop, workers=8):
    """Connections opening as fast as the kernel allows, until `stop` is set.

    The server answers `Connection: close`, so every request is a fresh accept — which is the
    point: the race needs the accept handler to be *running* at the instant stop() closes the
    acceptor, and an idle listener never runs it at all.
    """
    def loop():
        session = requests.Session()
        while not stop.is_set():
            try:
                session.get(base + "/api/health", timeout=5)
            except requests.RequestException:
                return                       # the server went away; that is the shutdown

    threads = [threading.Thread(target=loop, daemon=True) for _ in range(workers)]
    for t in threads:
        t.start()
    return threads


def _shutdown_under_load(binary, root, logpath, settle=0.8):
    """One whole lifecycle: come up, accept continuously, take SIGTERM mid-accept, exit."""
    server = TsanServer(binary, root, logpath)
    stop = threading.Event()
    threads = _accept_load(server.base, stop)
    try:
        time.sleep(settle)                   # the accept loop has to be genuinely busy first
        log = server.shutdown()              # SIGTERM, 0.3s, SIGTERM — with load still on
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=10)
    return log


def test_no_core_race_at_shutdown(tsan_binary, tmp_path):
    """No race at all — not "none attributable to app/".

    `SRV-FIX-024` had to weaken this criterion and record the core races it saw instead. This
    is the unweakened version: every ThreadSanitizer block in every shutdown log is a failure,
    whichever directory its frames are in.

    Read `SRV-FIX-024`'s report before trusting a green run here. TSan structurally
    under-reports in an asio pool — the scheduler's own mutex manufactures happens-before
    between handlers that merely ran at different moments — so this test buys its confidence
    with repetition and with load across the shutdown instant, not with one clean exit.
    """
    root = _repo(tmp_path / "repo", documents=8)
    races, shut_down = [], 0
    for i in range(SHUTDOWN_ITERATIONS):
        log = _shutdown_under_load(tsan_binary, root, tmp_path / f"tsan-shutdown-{i}.log")
        # A run that was killed rather than stopped never executed stop() and proves nothing.
        assert "Shutting down" in log, (
            f"iteration {i} never reached forceStop_(); this run watched nothing:\n{log[-1500:]}")
        shut_down += 1
        races += _races(log)

    assert shut_down == SHUTDOWN_ITERATIONS
    assert not races, (
        f"{len(races)} data race(s) across {SHUTDOWN_ITERATIONS} SIGTERM shutdowns under "
        f"accept load:\n\n" + "\n\n".join(races[:3]))


def _plain_server(root, log, **env):
    """A release-build server this test owns the lifetime of.

    Not the `serve` fixture: that one ends every instance with `proc.kill()`, which is exactly
    the reason shutdown was never behaviour anybody asserted (`ESCAPES.md`, SRV-FIX-025).
    """
    assert RELEASE_BINARY.exists(), f"build first — cd factory/server && ./build.sh build"
    port = _free_port()
    handle = log.open("w")
    proc = subprocess.Popen([str(RELEASE_BINARY), "--repo", str(root)],
                            env=dict(os.environ, FACTORY_HTTP_PORT=str(port), **env),
                            stdout=handle, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 10
    while time.time() < deadline:
        if proc.poll() is not None:
            handle.close()
            raise AssertionError(f"server exited {proc.returncode}\n{log.read_text()}")
        try:
            if requests.get(base + "/api/health", timeout=1).status_code == 200:
                return proc, port, base, handle
        except requests.RequestException:
            time.sleep(0.05)
    handle.close()
    raise AssertionError(f"server never came up\n{log.read_text()}")


def _still_listening(port):
    """Is anything on this machine LISTENing on `port`? Asked of the kernel.

    Not "can I bind it": the connections the test itself made leave the server side in
    TIME_WAIT for a minute, and a plain bind() fails on that while the listener is long gone.
    State 0A in /proc/net/tcp is LISTEN, and it is the property being asserted.
    """
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(table).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            f = line.split()
            if len(f) > 3 and f[3] == "0A" and int(f[1].split(":")[1], 16) == port:
                return True
    return False


def _port_is_free(port):
    """And a new server can have it — with SO_REUSEADDR, which is how asio binds."""
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def test_shutdown_still_closes_the_listener(tmp_path):
    """The fix may not turn "closes the acceptor" into "eventually, maybe".

    Making a stop() asynchronous to remove a race is how a listener learns to stay open, so
    the property is asserted from outside: after SIGTERM the process is gone and the port
    binds again. `test_bind.py` covers the other half — what it was reachable from.
    """
    root = _repo(tmp_path / "repo", documents=4)
    proc, port, base, handle = _plain_server(root, tmp_path / "listener.log")
    try:
        assert _still_listening(port), "the server is not actually holding the port"
        started = time.time()
        proc.send_signal(signal.SIGTERM)
        time.sleep(0.3)
        proc.send_signal(signal.SIGTERM)              # force: skip the 10s drain
        proc.wait(timeout=FORCE_BUDGET)
        elapsed = time.time() - started
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
        handle.close()
        raise AssertionError(
            f"the process was still running {FORCE_BUDGET}s after a forced shutdown\n"
            f"{(tmp_path / 'listener.log').read_text()[-2000:]}")
    finally:
        handle.close()

    assert proc.returncode is not None, "the process did not exit"
    assert elapsed < FORCE_BUDGET, f"forced shutdown took {elapsed:.1f}s"

    deadline = time.time() + 3
    while time.time() < deadline and _still_listening(port):
        time.sleep(0.05)
    assert not _still_listening(port), (
        f"something is still LISTENing on port {port} after the process exited")
    assert _port_is_free(port), (
        f"port {port} cannot be bound again; the next server would not start")


def test_the_threading_contract_is_in_the_header():
    """A contract that lives in a report is a contract the next product does not read.

    `core/` is vendored into other products. Everything downstream of this one read the code
    and guessed: twelve files say "the engine thread", singular, and hang "never add a mutex to
    event-driven code" on it — a rule that is only sound under the model they invented.

    So the assertions below are not a prose grep. Each fact the header states is checked
    against the code that implements it, and against the running process, so the paragraph
    cannot drift away from what it describes.
    """
    assert ENGINE_H.exists(), ENGINE_H
    header = ENGINE_H.read_text()
    source = ENGINE_CPP.read_text()

    # 1. The thread count, stated — and stated correctly.
    assert "ENGINE_THREADS" in header, (
        "engine.h does not name ENGINE_THREADS; the pool size is still folklore")
    default_in_code = re.search(r"ENGINE_THREADS(?:.|\n){0,300}?:\s*(\d+)\s*;", source)
    assert default_in_code, "could not read the pool default out of engine.cpp"
    n = default_in_code.group(1)
    words = {"1": "one", "2": "two", "3": "three", "4": "four"}
    assert re.search(rf"\b({n}|{words.get(n, n)})\b", header, re.I), (
        f"engine.cpp defaults the pool to {n} threads and engine.h does not say so")

    # 2. Where each lifecycle call happens. A component author's actual question, and the one
    #    catalog::Store and http::Server each had to work out from the sources.
    for call, must_say in (("init()", r"before|no thread|single"),
                           ("start()", r"after|already running|concurrent"),
                           ("stop()", r"signal|whichever|handler")):
        assert call in header, f"engine.h never says when {call} is called"
        assert re.search(rf"{re.escape(call)}(.|\n){{0,500}}?({must_say})", header, re.I), (
            f"engine.h names {call} but not the thread it runs on")

    # 3. The rule that stands on the count, and the one it displaces.
    assert re.search(r"strand", header, re.I), (
        "engine.h states a thread pool and not the strand rule that follows from it")
    assert re.search(r"never a mutex|not a mutex|no mutex", header, re.I), (
        "engine.h does not say which answer is wrong; 'use a strand' without 'never a mutex' "
        "is the sentence every downstream document already got half right")

    # 4. It is DESCRIPTION, so it is checked against the thing it describes. The header claims
    #    stop() is synchronous on the signal thread and that the io_context is stopped straight
    #    afterwards. If a later ticket changes either, this contract silently becomes a lie
    #    about the framework — which is the exact failure mode that produced SRV-FIX-025. So
    #    the claim is re-derived from engine.cpp rather than trusted.
    assert re.search(r"for\s*\(auto&\s*c\s*:\s*components_\)\s*c->stop\(\);\s*\n\s*"
                     r"service_\.stop\(\);", source), (
        "engine.cpp no longer stops components synchronously and then stops the io_context — "
        "the LIFECYCLE paragraph in engine.h now describes an engine that does not exist")
    assert re.search(r"stop\(\)(.|\n){0,600}?(may never run|might never run)", header, re.I), (
        "engine.h does not warn that work posted from stop() may never run; that is the one "
        "consequence a component author gets wrong when they move a stop() onto a strand")

    # 5. And the pool the header describes is the pool the process runs.
    assert re.search(r"pool", header, re.I), "engine.h does not call it a pool"
    assert not re.search(r"the engine thread is", header, re.I), (
        "engine.h asserts 'the engine thread is …' — the singular that cost a reverted ticket")


def test_shutdown_does_not_linger(tmp_path):
    """MANDATORY NEGATIVE — this is the failure the fix can actually cause.

    Making stop() asynchronous to remove a race is how a process learns to never quite finish,
    and that is worse than the race: a shutdown hang is every run after it, a shutdown race is
    one run in three. So: a server with an in-flight request, a held-open connection and a live
    WebSocket still exits, on one signal and on two, and this test fails if it hangs.
    """
    root = _repo(tmp_path / "repo", documents=6)

    for label, signals, budget in (("forced", 2, FORCE_BUDGET), ("drained", 1, DRAIN_BUDGET)):
        log = tmp_path / f"linger-{label}.log"
        proc, port, base, handle = _plain_server(root, log)
        held = []
        try:
            # An in-flight request: /api/slow answers at once and leaves a 2s task in the
            # worker pool, which core::Worker::stop() joins.
            assert requests.get(base + "/api/slow", timeout=5).status_code == 200

            # A connection the server is mid-read on and no client will ever finish.
            raw = socket.create_connection(("127.0.0.1", port), timeout=5)
            raw.sendall(b"GET /api/health HTTP/1.1\r\nHost: x\r\n")   # no terminator, ever
            held.append(raw)

            # A live WebSocket — a socket the server owns and is waiting on.
            ws = WsClient(host="127.0.0.1", port=port, timeout=5)
            held.append(ws.sock)

            started = time.time()
            for i in range(signals):
                proc.send_signal(signal.SIGTERM)
                if i + 1 < signals:
                    time.sleep(0.3)
            try:
                proc.wait(timeout=budget)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
                raise AssertionError(
                    f"{label} shutdown did not finish within {budget}s with a live "
                    f"connection and an in-flight request — the process lingers\n"
                    f"{log.read_text()[-2000:]}")
            elapsed = time.time() - started
        finally:
            for h in held:
                try:
                    h.close()
                except OSError:
                    pass
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
            handle.close()

        assert proc.returncode is not None, f"{label}: the process did not exit"
        assert elapsed < budget, f"{label} shutdown took {elapsed:.1f}s (budget {budget}s)"
        text = log.read_text()
        assert "Shutting down" in text, (
            f"{label}: the process exited without running forceStop_(); it did not shut "
            f"down, it died\n{text[-1500:]}")


# ── SRV-FIX-443 — a lane timer has never fired under TSan beside a handler ────────────────────
#
# Every case above drives the CATALOG and the shutdown. `lane::Store` keeps `lanes_`, `silence_`,
# `settling_`, `watches_` and `roles_` on `exec_` and posts every mutation onto it — and its
# sweep, silence and settle timers were built by `core::Task` on the BASE component's queue, a
# strand nothing else uses. Two strands serialise nothing between them. Nothing in this file
# reached that pairing, so the defect was invisible to a suite whose whole subject is races.

LANE_TSAN_SECONDS = 7.0
LANE_SILENCE_S = 130          # past lane::state::SILENCE_MS (120 s), twice


def _status_of(root):
    return subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=all"],
        capture_output=True, text=True, timeout=60).stdout


def _lane_repo(root):
    """A synthetic git repository a lane can actually be cut from. Never this one."""
    from test_lane import build_repo
    return build_repo(root, lanes_host=4, tickets=("TCK-001", "TCK-002", "TCK-003", "TCK-004"))


def _poll_lanes(server, seconds, workers=4):
    """E9, read over and over from `workers` clients, for `seconds`.

    Returns the failures and the last answer each client got — which is what criterion 1 asserts
    the released lane is absent from.
    """
    deadline = time.time() + seconds
    errors, last = [], {}

    def loop(i):
        session = requests.Session()
        seen = None
        while time.time() < deadline:
            try:
                r = session.get(server.base + "/api/lanes", timeout=30)
                if r.status_code != 200:
                    errors.append(f"GET /api/lanes -> {r.status_code}: {r.text[:200]}")
                    return
                seen = r.json()
            except (requests.RequestException, ValueError) as exc:
                errors.append(f"GET /api/lanes raised {exc}")
                return
        last[i] = seen

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(loop, range(workers)))
    return errors, last


def test_lane_timers_and_handlers_do_not_race(tsan_binary, tmp_path):
    """§1 criterion 1 — the sweep, the silence timers and the handlers, all at once, under TSan.

    Three lanes each run a process printing a line every 50 ms, so `stamp_` — which writes
    `lastOutputMs` and arms or disarms a silence task — is posted onto `exec_` about sixty times
    a second. Four clients read `E9`, which reads `lanes_` on the same strand. One lane is
    released at second three, which erases from `lanes_`, `holder_`, `queue_`, `byRequestId_`,
    `roles_` and `silence_`. The clock is advanced past the 120 s silence threshold twice, so the
    silence tasks really fire — and `retimeSilence` re-arms every one of them from the strand
    while they do. `SWEEP_INTERVAL_MS` is 5000 and the test clock does not move an asio timer, so
    seven seconds of real time is what it costs to see the sweep fire on its own; §1 pre-decided
    that cost.

    `ENGINE_THREADS=8` doubles the default pool, because a race needs two handlers genuinely
    running at once and four threads with this much posted work is thinner than it looks.
    """
    root = _lane_repo(tmp_path / "repo")
    before = _status_of(root)

    server = TsanServer(tsan_binary, root, tmp_path / "tsan-lanes.log",
                        ENGINE_THREADS="8", FACTORY_TEST_HOOKS="1")

    lanes = []
    for ticket in ("TCK-001", "TCK-002", "TCK-003"):
        claimed = requests.post(server.base + "/api/lanes",
                                json={"ticket_id": ticket, "workspace": "core"}, timeout=180)
        assert claimed.status_code == 201, claimed.text
        name = claimed.json()["lane"]
        started = requests.post(
            server.base + "/api/_test/lane-process",
            json={"lane": name, "command": "while true; do echo tick; sleep 0.05; done"},
            timeout=180)
        assert started.status_code == 202, started.text
        lanes.append(name)

    assert len(lanes) == 3, lanes

    def release_at(delay, name):
        time.sleep(delay)
        return requests.delete(server.base + f"/api/lanes/{name}?force=true", timeout=180)

    def advance_at(delay, seconds):
        time.sleep(delay)
        return requests.post(server.base + "/api/_test/clock",
                             json={"advance_s": seconds}, timeout=180)

    with ThreadPoolExecutor(max_workers=3) as pool:
        released = pool.submit(release_at, 3.0, lanes[0])
        first_quiet = pool.submit(advance_at, 1.5, LANE_SILENCE_S)
        second_quiet = pool.submit(advance_at, 5.0, LANE_SILENCE_S)
        errors, last = _poll_lanes(server, LANE_TSAN_SECONDS)
        gone = released.result(timeout=180)
        assert first_quiet.result(timeout=180).status_code == 202
        assert second_quiet.result(timeout=180).status_code == 202

    assert gone.status_code == 200, gone.text

    still = requests.get(server.base + "/api/lanes", timeout=180)
    assert still.status_code == 200, still.text
    for name in lanes[1:]:
        requests.delete(server.base + f"/api/lanes/{name}?force=true", timeout=180)

    log = server.shutdown()
    races = _races(log)

    assert not errors, f"requests failed while the lane timers ran: {errors[:3]}"
    assert not races, (
        f"{len(races)} data race(s) with three lanes producing output, E9 polled from four "
        f"clients, one release and two silence firings:\n\n" + "\n\n".join(races[:2]))

    # A run that claimed nothing, or whose processes never produced, is a clean log for the wrong
    # reason — the sweep only arms when `lanes_` is non-empty and `stamp_` only runs on output.
    assert last, "no client completed a full poll of E9; this run measured nothing"
    seen = {row["lane"] for answer in last.values() if answer for row in answer["lanes"]}
    assert set(lanes[1:]) <= seen, (
        f"E9 never listed the lanes this case claimed, so no handler ran beside a timer: {seen}")

    final = {row["lane"] for row in still.json()["lanes"]}
    assert lanes[0] not in final, (
        f"{lanes[0]} was released at second three and E9 still lists it: {sorted(final)}")

    after = _status_of(root)
    assert after == before, f"the main checkout changed:\nbefore:\n{before}\nafter:\n{after}"


# ── SRV-FIX-469 — main added a timer on the base strand while SRV-FIX-443 was in QA ───────────
#
# `SRV-FEAT-454` landed `lane::Store::armWords_` on `main` in `6296fa4d`, building its task as
# `core::Task {this, …}` — the constructor SRV-FIX-443 deletes, on `core::Component`'s own queue,
# the strand no component posts a handler onto. Its handler erases from `words_`, calls `find_`
# and calls `publishWords_`, which reads `lastWords` and fires the handlers — every one of them
# `lane::Store` state that `setWords`, `list` and `release` post onto `exec_`.
#
# SRV-FIX-443's own TSan case could not have driven this: the member did not exist when it was
# written. This is that case widened by the member main added.

WORDS_COALESCE_MS = 2000
WORDS_TSAN_SECONDS = 12.0


def _words_repo(root):
    """A repository whose agent replays committed captures on demand — the real words path.

    Not a stand-in for the parser: the entries this feeds are bytes from the committed CLI
    captures, they go through `agent::transcript`, `agent::Store`'s `setLaneWords` and
    `api::Manager`'s hop into `lane::Store::setWords`, which is the only caller there is.
    """
    from test_lane import build_repo, git, WORDS_AGENT_SH
    root = build_repo(root, lanes_host=4, agent={"command": "/bin/sh agent.sh"},
                      tickets=("TCK-001", "TCK-002", "TCK-003"))
    (root / "agent.sh").write_text(WORDS_AGENT_SH)
    git(root, "add", "agent.sh")
    git(root, "commit", "-m", "an agent that replays a committed capture")
    return root


def test_the_words_timer_does_not_race_the_handlers(tsan_binary, tmp_path):
    """§1 criterion 2 of SRV-FIX-469 — `words_` and its timer, under TSan, beside the handlers.

    Two lanes, each running an agent that emits captured transcript entries faster than
    `WORDS_COALESCE_MS`, so `setWords` really coalesces: the first entry publishes and arms the
    task, every entry inside the window sets `wordsPending` and returns, and the task fires,
    erases its own map entry, republishes and re-arms. Four clients poll `E9`, which reads
    `lastWords` off the same strand, and one lane is released mid-flight so `words_.erase` runs
    from the release path while the timer is armed.

    The assertion is TSan's, plus the answer: the surviving lane's `last_words` is the LAST entry
    it was fed. A green race report on a lane that published nothing proves nothing, so the words
    have to arrive as well.
    """
    from test_lane import one_capture, expected_words

    end_turn = one_capture("result")["raw"]
    root = _words_repo(tmp_path / "repo")
    before = _status_of(root)

    server = TsanServer(tsan_binary, root, tmp_path / "tsan-words.log",
                        ENGINE_THREADS="8", FACTORY_TEST_HOOKS="1")

    def claim(ticket):
        r = requests.post(server.base + "/api/lanes",
                          json={"ticket_id": ticket, "workspace": "core"}, timeout=180)
        assert r.status_code == 201, r.text
        return r.json()

    def start_run(lane):
        r = requests.post(server.base + f"/api/lanes/{lane['lane']}/runs", json={}, timeout=180)
        assert r.status_code == 201, r.text
        return r.json()

    def log_of(lane):
        try:
            return (Path(lane["worktree"]) / "agent-log.txt").read_text()
        except OSError:
            return ""

    def wait_log(lane, needle, timeout=90):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if needle in log_of(lane):
                return True
            time.sleep(0.1)
        raise AssertionError(f"{needle!r} never reached {lane['lane']}'s log:\n{log_of(lane)}")

    lanes = [claim("TCK-001"), claim("TCK-002")]
    for lane in lanes:
        start_run(lane)
        wait_log(lane, "ready")

    # Distinct captures, so "the last one" is a value and not a coincidence.
    shapes = [one_capture("tool_use_plain", "cli-stream-2026-07-27-simple.jsonl", 5),
              one_capture("text")]

    def feed(lane, name, block):
        """One step file, and the capture that ENDS the turn.

        Without the trailing `result` frame the turn never ends and the next message is HELD
        rather than delivered (`SPEC.md` F14), so the second entry never reaches the parser and
        the coalescing this case is about never happens.
        """
        (Path(lane["worktree"]) / name).write_text(block["raw"] + "\n" + end_turn + "\n")
        return requests.post(server.base + f"/api/lanes/{lane['lane']}/messages",
                             json={"text": f"emit:{name}"}, timeout=180)

    fed_last, fed_count = {}, {}

    def drive(lane):
        """Entries closer together than the coalescing window, for the WHOLE window of the run.

        Feeding a fixed handful is not enough to pin this: the task fires once
        `WORDS_COALESCE_MS` after the first entry, so a run that stops feeding at about that
        moment gives the timer and the handlers almost no chance to overlap, and a version of
        this case that fed six entries was GREEN with the fix reverted. It has to keep feeding
        across several firings of the timer while `E9` is read the whole time.
        """
        deadline = time.time() + WORDS_TSAN_SECONDS - 0.5
        i = 0
        while time.time() < deadline:
            block = shapes[i % len(shapes)]
            name = f"step{i}.jsonl"
            i += 1
            r = feed(lane, name, block)
            if r.status_code != 200:
                break
            if r.json().get("status") != "delivered":
                continue
            try:
                wait_log(lane, f"emitted:emit:{name}", timeout=30)
            except AssertionError:
                # One of these two lanes is force-released mid-flight, which is the point of the
                # case: its agent goes away under the driver. That is not a failure — but a
                # driver that swallowed it for a lane still LISTED would hide a real stall, so
                # the tool is asked which it was.
                if still_claimed(lane):
                    raise
                break
            fed_last[lane["lane"]] = block
        fed_count[lane["lane"]] = i

    def still_claimed(lane):
        try:
            rows = requests.get(server.base + "/api/lanes", timeout=60).json()["lanes"]
        except (requests.RequestException, ValueError):
            return False
        return any(r["lane"] == lane["lane"] for r in rows)

    def release_at(delay, lane):
        time.sleep(delay)
        return requests.delete(server.base + f"/api/lanes/{lane['lane']}?force=true", timeout=180)

    survivor = lanes[1]
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            drivers = [pool.submit(drive, lane) for lane in lanes]
            released = pool.submit(release_at, 3.0, lanes[0])
            errors, last = _poll_lanes(server, WORDS_TSAN_SECONDS)
            for d in drivers:
                d.result(timeout=180)
            gone = released.result(timeout=180)

        assert gone.status_code == 200, gone.text

        rows = requests.get(server.base + "/api/lanes", timeout=180).json()["lanes"]
        mine = [r for r in rows if r["lane"] == survivor["lane"]]
        assert mine, f"the surviving lane is not in E9: {[r['lane'] for r in rows]}"
    finally:
        # Through the tool, always — and even when the case is failing, or the run it started
        # outlives it and `no_leaked_processes` reports the leak instead of the defect.
        for lane in lanes:
            try:
                requests.delete(server.base + f"/api/lanes/{lane['lane']}?force=true", timeout=180)
            except requests.RequestException:
                pass
        log = server.shutdown()
    races = _races(log)

    assert not errors, f"requests failed while the words timer ran: {errors[:3]}"

    worded = [r for r in races if "words_" in r or "armWords_" in r or "publishWords_" in r]
    assert not worded, (
        f"{len(worded)} data race(s) naming words_ with two lanes coalescing entries, E9 polled "
        f"from four clients and one lane released mid-flight:\n\n" + "\n\n".join(worded[:2]))
    assert not races, (
        f"{len(races)} data race(s) in the same run:\n\n" + "\n\n".join(races[:2]))

    # The run has to have produced words at all, or a clean log is clean for the wrong reason —
    # and it has to have fed across SEVERAL firings of the coalescing timer, or the timer and the
    # handlers never overlapped and the absence of a race says nothing about the executor.
    assert survivor["lane"] in fed_last, "no entry was ever fed to the surviving lane"
    firings = (WORDS_TSAN_SECONDS * 1000.0) / WORDS_COALESCE_MS
    assert firings >= 3, f"the run is too short for the coalescing timer to fire: {firings}"
    assert fed_count.get(survivor["lane"], 0) >= 4, (
        f"only {fed_count.get(survivor['lane'], 0)} entries reached the surviving lane, so the "
        f"words timer and the handlers barely overlapped and this run measured little")
    want = expected_words(fed_last[survivor["lane"]]["block"])
    got = mine[0].get("last_words")
    assert got, f"E9 carries no last_words for {survivor['lane']}: {mine[0]}"
    assert got.get("kind") == want["kind"] and got.get("text") == want["text"], (
        f"E9's last_words is not the last entry fed: served {got}, last fed {want}")

    after = _status_of(root)
    assert after == before, f"the main checkout changed:\nbefore:\n{before}\nafter:\n{after}"
