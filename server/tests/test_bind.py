"""What this process is reachable from.

SRV-FEAT-003 shipped a server that listened on 0.0.0.0 on two ports. Every gate it ran asked
whether the code worked; none asked what the code was exposed to. SPEC.md §5 says loopback only,
and §1 gives that sentence as the reason the tool needs no authentication at all — so the bind
address is not a detail, it is the whole security model.

These tests do not name a port. They ask the kernel what is listening and judge all of it.

`survey()` covers a stated scope, and returns that scope alongside the result: which protocols it
read, which processes it walked, and over what window it sampled. SRV-FIX-020 asserted the same
kind of thing and *described* it as covering every listener that could ever appear; its own QA
session opened three that it missed — a UNIX/UDP socket, a listener held by a child process, and
one opened after the instant it sampled. A check whose scope is invisible is a check whose gaps
are invisible, so every assertion below quotes the scope it was decided on: "no exposed listener,
among tcp/udp/unix sockets held by pid 1234, sampled 4x over 1.50s ending 14:02:11" cannot be
mistaken for "nothing is exposed".

The scope is deliberately not "everything". It is one process tree, five /proc tables, one
sampling window — and what it does not cover is written in `Scope`, not left for the next reader
to discover the way SRV-FIX-023 had to.
"""

import ipaddress
import os
import re
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from conftest import WsClient


MANIFEST = {
    "factory": "0.1",
    "workspaces": {"only": {"path": "."}},
    "docs": {"epics": "docs/epics", "tickets": "docs/tickets", "reports": "docs/reports"},
}

# Every /proc table that records something a packet or a connect() can arrive on. Read directly,
# never through `ss` or `lsof`: the machine where the tool is missing is the machine where the
# check quietly stops running.
TABLES = ("/proc/net/tcp", "/proc/net/tcp6", "/proc/net/udp", "/proc/net/udp6", "/proc/net/unix")

# The sampling budget. A listener opened lazily is invisible to a check that looks once, so the
# survey samples until a deadline — but a suite made slow by sleeping is a suite that stops being
# run. 1.5 s, paid by the two tests that need it and by nothing else; the rest sample once and say
# so. Overridable for a slower machine, and the value used is reported in the scope either way.
SETTLE = float(os.environ.get("FACTORY_LISTENER_WINDOW", "1.5"))

SAMPLE_INTERVAL = 0.05


def _hex_to_ip(hex_addr):
    """/proc/net/tcp writes the address as little-endian hex."""
    if len(hex_addr) == 8:
        b = bytes.fromhex(hex_addr)[::-1]
        return socket.inet_ntop(socket.AF_INET, b)
    groups = [bytes.fromhex(hex_addr[i:i + 8])[::-1] for i in range(0, 32, 8)]
    return socket.inet_ntop(socket.AF_INET6, b"".join(groups))


def is_loopback(ip):
    """127.0.0.0/8 and ::1 — including the IPv4-mapped form, which `ipaddress` calls global."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        addr = mapped
    return addr.is_loopback


@dataclass(frozen=True)
class Listener:
    """One thing the kernel will deliver to, and the pid holding it."""

    proto: str                  # tcp, tcp6, udp, udp6, unix
    ip: str                     # "" for a UNIX socket
    port: int                   # 0 for a UNIX socket
    path: str                   # "" for a network socket
    pid: int

    @property
    def is_local(self):
        """The loopback rule, applied to every protocol the survey reads.

        A UNIX socket is a filesystem path, not an interface: nothing off this machine can
        connect to it, so it is local by construction. It is still reported, because "the check
        saw it and judged it local" and "the check never looked" are different sentences and only
        one of them is honest.
        """
        if self.proto == "unix":
            return True
        return is_loopback(self.ip)

    def __str__(self):
        where = self.path if self.proto == "unix" else f"{self.ip}:{self.port}"
        return f"{self.proto} {where} (pid {self.pid})"


@dataclass(frozen=True)
class Scope:
    """What the survey covered — returned with the result, never assumed by the reader."""

    pids: tuple
    tables: tuple
    first: float
    last: float
    samples: int
    window: float

    @property
    def span(self):
        """The window actually covered, first sample to last.

        Not `window`, which is only what was asked for. A survey that sampled once and reports
        "over 1.50s" is a scope overstating itself — the same shape of claim this whole file
        exists to stop making.
        """
        return self.last - self.first

    def __str__(self):
        protos = "/".join(t.rsplit("/", 1)[1] for t in self.tables)
        pids = ", ".join(str(p) for p in self.pids)
        at = time.strftime("%H:%M:%S", time.localtime(self.last))
        return (f"among {protos} sockets held by pid {pids}, "
                f"sampled {self.samples}x over {self.span:.2f}s ending {at}")


@dataclass(frozen=True)
class Survey:
    """Listeners, and the scope they were found in."""

    listeners: tuple
    scope: Scope

    @property
    def exposed(self):
        return tuple(x for x in self.listeners if not x.is_local)

    def says(self, headline):
        """A sentence no reader can mistake for a claim about everything."""
        return f"{headline} — {self.scope}"


def _children_of(pid):
    """(children, the kernel answered). /proc/<pid>/task/*/children is a config option."""
    kids, answered = set(), False
    try:
        tasks = list(Path(f"/proc/{pid}/task").iterdir())
    except OSError:
        return kids, False
    for task in tasks:
        try:
            kids.update(int(x) for x in (task / "children").read_text().split())
            answered = True
        except OSError:
            continue
    return kids, answered


def _tree_by_ppid(pid):
    """Every descendant, from the PPid of every process. Works without CONFIG_PROC_CHILDREN."""
    children = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            for line in (entry / "status").read_text().splitlines():
                if line.startswith("PPid:"):
                    children.setdefault(int(line.split()[1]), []).append(int(entry.name))
                    break
        except OSError:
            continue
    seen, stack = {pid}, [pid]
    while stack:
        for kid in children.get(stack.pop(), ()):
            if kid not in seen:
                seen.add(kid)
                stack.append(kid)
    return seen


def process_tree(pid):
    """This pid and every descendant of it.

    One /proc/<pid>/fd was the whole scope until SRV-FIX-023. Spawning run processes is what this
    product does from wave 3 on, so a listener held by a child is the shape most likely to appear
    and the one the old check could not see.
    """
    root_kids, answered = _children_of(pid)
    if not answered:
        return _tree_by_ppid(pid)
    seen, stack = {pid} | root_kids, list(root_kids)
    while stack:
        kids, _ = _children_of(stack.pop())
        for kid in kids - seen:
            seen.add(kid)
            stack.append(kid)
    return seen


def _socket_inodes(pids):
    """inode → the pid holding it, for every process in the tree."""
    owner = {}
    for pid in pids:
        try:
            fds = list(Path(f"/proc/{pid}/fd").iterdir())
        except OSError:
            continue                        # exited mid-walk, or not ours to read
        for fd in fds:
            try:
                target = os.readlink(fd)
            except OSError:
                continue
            if target.startswith("socket:["):
                owner.setdefault(target[8:-1], pid)
    return owner


def _net_sockets(owner, tables):
    """TCP listeners and bound UDP sockets. UDP has no LISTEN state — bound is reachable.

    Returns (found, tables actually read). A table this kernel does not offer is not coverage,
    and the scope must not claim it.
    """
    found, read = [], []
    for table in tables:
        proto = table.rsplit("/", 1)[1]
        if proto == "unix":
            continue
        try:
            lines = Path(table).read_text().splitlines()[1:]
        except OSError:
            continue
        read.append(table)
        for line in lines:
            f = line.split()
            if len(f) < 10:
                continue
            if proto.startswith("tcp") and f[3] != "0A":       # 0A = TCP_LISTEN
                continue
            pid = owner.get(f[9])
            if pid is None:
                continue
            addr, port = f[1].rsplit(":", 1)
            port = int(port, 16)
            if proto.startswith("udp") and port == 0:
                continue                    # unbound: nothing can be addressed to it
            found.append(Listener(proto, _hex_to_ip(addr), port, "", pid))
    return found, read


def _unix_sockets(owner, tables):
    """UNIX-domain sockets in the accepting state. Returns (found, tables actually read)."""
    found = []
    if "/proc/net/unix" not in tables:
        return found, []
    try:
        lines = Path("/proc/net/unix").read_text().splitlines()[1:]
    except OSError:
        return found, []
    for line in lines:
        f = line.split()
        if len(f) < 7:
            continue
        try:
            flags = int(f[3], 16)
        except ValueError:
            continue
        if not flags & 0x10000:             # SO_ACCEPTCON
            continue
        pid = owner.get(f[6])
        if pid is None:
            continue
        found.append(Listener("unix", "", 0, f[7] if len(f) > 7 else "(unnamed)", pid))
    return found, ["/proc/net/unix"]


def survey(pid, window=0.0, interval=SAMPLE_INTERVAL, tables=TABLES):
    """Everything this process tree is listening on, and the scope that answer covers.

    Sampled until `window` elapses and unioned, because a listener that existed at any moment in
    the window was exposed at that moment — closing it afterwards does not unsay it. `window=0`
    is one sample, which is honest as long as the scope says so, and it does.
    """
    deadline = time.monotonic() + window
    seen, pids, read, samples = {}, set(), set(), 0
    first = time.time()
    while True:
        tree = process_tree(pid)
        pids |= tree
        owner = _socket_inodes(tree)
        net, net_read = _net_sockets(owner, tables)
        unix, unix_read = _unix_sockets(owner, tables)
        read.update(net_read + unix_read)
        for listener in net + unix:
            seen[listener] = None
        samples += 1
        last = time.time()
        if time.monotonic() >= deadline:
            break
        time.sleep(interval)
    covered = tuple(t for t in tables if t in read)
    return Survey(tuple(seen), Scope(tuple(sorted(pids)), covered, first, last, samples, window))


def narrow_tcp_listeners(pid):
    """SRV-FIX-020's enumeration, kept verbatim as the control for the negative test.

    Not used to judge anything. It exists so `test_each_new_blindness_is_demonstrated_red_first`
    can show, on every run, that each widening is load-bearing: this returns [] for all three of
    the listeners below, and `survey` returns them. Delete it and the three fixes become three
    changes nobody can prove were ever needed — which is how the original claim got made.
    """
    inodes = set()
    for fd in Path(f"/proc/{pid}/fd").iterdir():
        try:
            target = os.readlink(fd)
        except OSError:
            continue
        if target.startswith("socket:["):
            inodes.add(target[8:-1])

    found = []
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(table).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            f = line.split()
            if len(f) < 10 or f[3] != "0A":
                continue
            if f[9] not in inodes:
                continue
            addr, port = f[1].rsplit(":", 1)
            found.append((_hex_to_ip(addr), int(port, 16)))
    return found


@pytest.fixture
def server(repo, serve):
    return serve(repo.manifest(MANIFEST))


def test_every_listener_is_on_loopback(server):
    """The assertion SPEC.md §5 states and nothing checked."""
    s = survey(server.pid, window=SETTLE)
    assert s.listeners, s.says("no listening socket found for this process — the check is broken")
    assert not s.exposed, s.says(
        f"reachable off this machine: {[str(x) for x in s.exposed]}. SPEC.md §5 is 'loopback "
        f"only', and §1 gives that as the reason this tool has no authentication at all")


def test_the_server_opens_exactly_one_listener(server):
    """One port. The socket is a path on it, not a listener of its own.

    A second listener is a second surface to secure, a second proxy rule for the client, and a
    second origin for the browser — and the first version of this server had all three.

    One sample, not a window: this counts, and a resolver's UDP socket opened for a moment would
    make a union over time report two. The count is of what is held now; what is held over the
    window is `test_every_listener_is_on_loopback`'s question.
    """
    s = survey(server.pid)
    assert len(s.listeners) == 1, s.says(
        f"expected one listener, found {[str(x) for x in s.listeners]}")


def test_the_socket_is_a_path_on_that_port(server):
    """/ws upgrades on the HTTP port, and greets."""
    port = int(server.base.rsplit(":", 1)[1])
    c = WsClient(host="127.0.0.1", port=port, path="/ws")
    try:
        assert c.recv()["m"] == "hello"
    finally:
        c.close()


def test_an_upgrade_to_an_unclaimed_path_is_refused(server):
    """Not silently answered as a GET: a client that asked for a socket and got JSON cannot
    tell whether the path was wrong or the server was."""
    port = int(server.base.rsplit(":", 1)[1])
    with pytest.raises(AssertionError):
        WsClient(host="127.0.0.1", port=port, path="/nope")


def test_cors_does_not_hand_the_response_to_any_page(server):
    """Loopback does not make the browser safe: a page on any site the developer visits can
    reach 127.0.0.1, and `*` tells the browser to let it read what comes back."""
    origin = server.get("/api/health").headers.get("Access-Control-Allow-Origin")
    assert origin != "*", "any origin may read this API's responses"


def test_the_check_would_have_caught_it(repo, serve):
    """The negative that keeps the three above honest.

    A check that only ever runs against a correct server proves nothing about the check. This
    starts one deliberately bound wide and asserts the detection fires — so if the survey
    silently stops finding sockets, this test goes red instead of everything going green.
    """
    wide = serve(repo.manifest(MANIFEST), FACTORY_BIND="0.0.0.0")
    s = survey(wide.pid)
    assert s.listeners, s.says("no listening socket found — the check is broken, not the server")
    assert s.exposed, s.says(
        f"a server told to bind 0.0.0.0 looked loopback-only: {[str(x) for x in s.listeners]}")


# ── SRV-FIX-023: the three the old check could not see ──────────────────────────────────────

UNIX_AND_UDP = """
import socket, tempfile, time
p = tempfile.mktemp(suffix='.sock')
u = socket.socket(socket.AF_UNIX); u.bind(p); u.listen(1)
d = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); d.bind(('0.0.0.0', 0))
print(p, d.getsockname()[1], flush=True)
time.sleep(30)
"""

PARENT_WITH_LISTENING_CHILD = """
import subprocess, sys, time
c = subprocess.Popen([sys.executable, '-c',
  "import socket, time; s = socket.socket(); s.bind(('0.0.0.0', 0)); s.listen(1);"
  "print(s.getsockname()[1], flush=True); time.sleep(30)"], stdout=subprocess.PIPE, text=True)
print(c.stdout.readline().strip(), c.pid, flush=True)
time.sleep(30)
"""

LISTENER_OPENED_LATER = """
import socket, sys, time
time.sleep(float(sys.argv[1]))
s = socket.socket(); s.bind(('0.0.0.0', 0)); s.listen(1)
print(s.getsockname()[1], flush=True)
time.sleep(30)
"""

OPENED_AFTER = 0.5


@pytest.fixture
def spawn():
    """Processes that hold the listeners under test, torn down by their process GROUP.

    The group matters: one shape here is a parent whose child holds the wide socket, and killing
    only the parent would orphan a 0.0.0.0 listener for the rest of the run. Every wide bind is
    on an ephemeral port and dies with this fixture.
    """
    started = []

    def _spawn(src, *args):
        return _track(subprocess.Popen([sys.executable, "-c", src, *[str(a) for a in args]],
                                       stdout=subprocess.PIPE, text=True, start_new_session=True))

    def _track(proc):
        started.append(proc)
        return proc

    yield _spawn

    for proc in started:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except OSError:
            proc.kill()
        proc.wait(timeout=5)
        if proc.stdout is not None:
            proc.stdout.close()


def test_a_unix_or_udp_listener_is_seen(spawn):
    """Only /proc/net/tcp{,6} was ever read. A UNIX listener and a UDP socket bound to every
    interface were both invisible, and one of them is reachable from the LAN."""
    proc = spawn(UNIX_AND_UDP)
    path, udp_port = proc.stdout.readline().split()
    s = survey(proc.pid)

    unix = [x for x in s.listeners if x.proto == "unix" and x.path == path]
    assert unix, s.says(f"a UNIX-domain listener at {path} is held by pid {proc.pid}, unreported")

    udp = [x for x in s.listeners if x.proto.startswith("udp") and x.port == int(udp_port)]
    assert udp, s.says(f"a UDP socket bound to 0.0.0.0:{udp_port} is held by pid {proc.pid}, "
                       f"unreported")

    # judged by the same loopback rule, not merely listed
    assert udp[0] in s.exposed, s.says(f"{udp[0]} is bound to every interface and was judged local")
    assert unix[0].is_local, s.says(f"{unix[0]} is a filesystem path and was judged reachable")


def test_a_child_process_listener_is_seen(spawn):
    """The criterion this ticket exists for.

    Wave 3 starts agent runs as child processes. A check that walks one /proc/<pid>/fd goes blind
    the day the product starts doing the dangerous thing, and goes blind silently.
    """
    proc = spawn(PARENT_WITH_LISTENING_CHILD)
    child_port, child_pid = proc.stdout.readline().split()
    s = survey(proc.pid)

    assert int(child_pid) in s.scope.pids, s.says(
        f"the child (pid {child_pid}) was never walked")
    wide = [x for x in s.exposed if x.port == int(child_port)]
    assert wide, s.says(
        f"pid {proc.pid} has a child (pid {child_pid}) listening on 0.0.0.0:{child_port}, "
        f"and the survey reports {[str(x) for x in s.listeners]}")
    assert wide[0].pid == int(child_pid), s.says(
        f"the exposure was reported against the wrong pid: {wide[0]}")


def test_a_listener_opened_later_is_seen(spawn):
    """A snapshot answers for the instant it was taken and no other.

    The window this covers is SETTLE seconds from the call — 1.50 s by default, stated here and
    restated by the scope in every failure message. A listener opened after it is still missed,
    which is a limit, not a secret.
    """
    proc = spawn(LISTENER_OPENED_LATER, OPENED_AFTER)
    assert OPENED_AFTER < SETTLE, f"the fixture opens at {OPENED_AFTER}s, outside a {SETTLE}s window"

    s = survey(proc.pid, window=SETTLE)
    port = int(proc.stdout.readline())

    assert s.scope.samples > 1, s.says(f"one sample cannot cover a {SETTLE:.2f}s window")
    assert [x for x in s.exposed if x.port == port], s.says(
        f"pid {proc.pid} opened 0.0.0.0:{port} {OPENED_AFTER}s after start, and the survey "
        f"reports {[str(x) for x in s.listeners]}")


def test_the_window_is_stated_not_assumed(repo, serve):
    """A check whose scope is invisible is a check whose gaps are invisible.

    "No exposed listener" is a claim about everything. "No exposed listener, among tcp/udp/unix
    sockets held by pid 1234, sampled 30x over 1.50s ending 14:02:11" is a claim someone can
    check, and can notice is too narrow. Every assertion in this file quotes the second one.
    """
    wide = serve(repo.manifest(MANIFEST), FACTORY_BIND="0.0.0.0")
    s = survey(wide.pid, window=SETTLE)
    message = s.says("exposed")

    assert s.scope.pids and wide.pid in s.scope.pids     # the process set it covered
    assert s.scope.last >= s.scope.first                 # the moment it covered
    assert s.scope.samples >= 1 and s.scope.window == SETTLE

    assert str(wide.pid) in message, message
    for proto in ("tcp", "tcp6", "udp", "udp6", "unix"):
        assert proto in message, f"{proto} is read and the message does not say so: {message}"
    assert re.search(r"\d\d:\d\d:\d\d", message), f"no moment in: {message}"

    # The window in the message is the one covered, not the one asked for. A survey that sampled
    # a single instant and printed the budget would be a scope overstating itself.
    covered = re.search(r"sampled (\d+)x over (\d+\.\d\d)s", message)
    assert covered, f"no window in: {message}"
    assert float(covered.group(2)) >= SETTLE * 0.95, (
        f"the message claims {covered.group(2)}s of a {SETTLE:.2f}s budget it did not cover: "
        f"{message}")

    # The scope is derived from what was read, not a constant beside it. A scope that cannot
    # shrink is decoration: it would go on claiming five tables the day one stops being read,
    # which is the exact shape of the claim this ticket exists to correct.
    narrowed = survey(wide.pid, tables=("/proc/net/tcp",))
    assert narrowed.scope.tables == ("/proc/net/tcp",), narrowed.says("scope did not narrow")
    assert "udp" not in str(narrowed.scope), (
        f"the scope claims a table it did not read: {narrowed.scope}")


@pytest.mark.parametrize("shape", ["unix-and-udp", "child-process", "opened-later"])
def test_each_new_blindness_is_demonstrated_red_first(spawn, shape):
    """The mandatory negative: every widening, proved load-bearing on every run.

    Each shape opens a real listener, the OLD narrow enumeration is asserted to miss it, and the
    new survey to catch it. Without this the ticket is three fixes nobody can prove were needed —
    and an unverified claim about coverage is exactly what it is fixing.
    """
    if shape == "unix-and-udp":
        proc = spawn(UNIX_AND_UDP)
        path, udp_port = proc.stdout.readline().split()
        old, new = narrow_tcp_listeners(proc.pid), survey(proc.pid)
        wanted = [x for x in new.listeners
                  if x.path == path or (x.proto.startswith("udp") and x.port == int(udp_port))]
        described = f"a UNIX listener at {path} and udp 0.0.0.0:{udp_port}"
    elif shape == "child-process":
        proc = spawn(PARENT_WITH_LISTENING_CHILD)
        child_port, child_pid = proc.stdout.readline().split()
        old, new = narrow_tcp_listeners(proc.pid), survey(proc.pid)
        wanted = [x for x in new.exposed if x.port == int(child_port)]
        described = f"a child (pid {child_pid}) holding 0.0.0.0:{child_port}"
    else:
        proc = spawn(LISTENER_OPENED_LATER, OPENED_AFTER)
        old = narrow_tcp_listeners(proc.pid)             # sampled before it opens, as SRV-FIX-020 did
        new = survey(proc.pid, window=SETTLE)
        port = int(proc.stdout.readline())
        wanted = [x for x in new.exposed if x.port == port]
        described = f"0.0.0.0:{port} opened {OPENED_AFTER}s after start"

    assert not old, (
        f"{described}: SRV-FIX-020's enumeration returned {old} — if it now sees this, the "
        f"control is wrong and this test proves nothing")
    assert wanted, new.says(f"{described}: the widened survey missed it")
