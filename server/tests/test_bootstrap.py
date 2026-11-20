"""SRV-FEAT-001 — the spine.

Four assertions, and every one of them is here because of something that went wrong before
this project had a spine to test.
"""

import pytest
import time
import requests

from conftest import api_base, WsClient
from test_confine import argv_recorder                             # noqa: F401



def test_health(api):
    """E1 — the server answers, and says which manifest major it speaks."""
    r = api.get("/api/health")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("factory") == "0.1", body

    # still up afterwards: a health check that kills the process is not a health check
    assert api.get("/api/health").status_code == 200


def test_ws_push(ws):
    """The socket carries JSON text frames, and the server speaks first.

    This is the assertion the whole stack choice rests on. The vendored core/ws turned out to
    be nanopb over a type envelope with auth fields, so the factory carries its own Beast
    wrapper — and that wrapper is unproven until a real client reads a real frame off it.
    """
    msg = ws.recv()
    assert msg.get("m") == "hello", msg
    assert msg.get("factory") == "0.1", msg


def test_engine_not_blocked(api):
    """A slow job must not stall the engine thread.

    Never block the engine is the rule this codebase breaks first, and process supervision —
    the factory's whole job — is exactly where it gets broken. The test exists before there
    is anything to block.
    """
    import threading
    slow_done = threading.Event()

    def slow():
        requests.get(api_base() + "/api/slow", timeout=10)
        slow_done.set()

    t = threading.Thread(target=slow, daemon=True)
    t.start()
    time.sleep(0.3)                       # let the slow job get going

    started = time.time()
    r = api.get("/api/health")
    elapsed = time.time() - started

    assert r.status_code == 200
    assert elapsed < 1.0, f"health took {elapsed:.2f}s while a slow job ran — the engine is blocked"
    t.join(timeout=10)
    assert slow_done.is_set(), "the slow job never finished"


def test_unknown_route_is_refused(api):
    """Negative: an unknown path is a clean 404, and the engine survives it."""
    r = api.get("/api/there-is-no-such-thing")
    assert r.status_code == 404, r.text
    assert api.get("/api/health").status_code == 200, "the engine died on an unknown route"


def test_second_socket_also_gets_hello():
    """Negative: the greeting is per connection, not a one-shot fired at the first client.

    A server that greets only its first client looks identical to a working one until the
    second tab opens.
    """
    a = WsClient()
    b = WsClient()
    try:
        assert a.recv().get("m") == "hello"
        assert b.recv().get("m") == "hello"
    finally:
        a.close()
        b.close()


# ── SRV-FIX-532 cluster E2 — where the suite BUILDS a repository ──────────────────────────────
#
# `SRV-FIX-460`. Every case that claims a lane builds a repository first, and `git worktree add`
# then cuts a checkout inside it. If that repository sits under a path the lane policy makes
# WRITABLE — the invented scratch, the invented home — the suite is starting a lane inside a lane,
# and what it measures afterwards is the composition of two sandboxes rather than the product.
#
# The rule is asked of the POLICY the product builds, never of a directory name: a name is what a
# reader believes, and the policy is what a lane got.


def invented_for_a_lane(argv):
    """The paths a lane's policy invents FRESH for it — its own home and its own scratch.

    Read off the argv the tool BUILT, not off `confine.cpp`'s constants: `invent` walks past any
    of them a path it has to carry in already occupies, so a lane cut inside a lane is given
    `/home/lane2` and `/tmp/factory2` and the constants name nothing that lane got.

    These two are the paths that matter to a fixture, and `--tmpfs /tmp` is NOT one of them: the
    policy mounts that as a base and then binds the repository under test back on top of it, so a
    fixture there is visible inside the lane. What is invented is mounted fresh and bound back
    from nowhere — anything the suite built under one of those is simply not there for the lane,
    and `git worktree add` into it is a lane inside a lane.
    """
    import re
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app" / "lane" / "confine.cpp").read_text()
    roots = dict(re.findall(r'constexpr const char\* (HOME_INSIDE|SCRATCH_INSIDE)\s*=\s*"([^"]+)"',
                            source))
    assert set(roots) == {"HOME_INSIDE", "SCRATCH_INSIDE"}, (
        f"the confinement's invented paths are no longer two named constants: {roots}")
    shapes = [re.compile(rf"^{re.escape(v)}\d*$") for v in roots.values()]

    out, i = [], 0
    while i < len(argv):
        token = argv[i]
        if token in ("--tmpfs", "--dir"):
            where = argv[i + 1]
            if any(shape.match(where) for shape in shapes):
                out.append(where)
            i += 2
        elif token in ("--bind", "--ro-bind", "--ro-bind-try", "--symlink", "--bind-try"):
            i += 3
        else:
            i += 1
    assert len(out) >= 2, (
        f"the policy invents {out} for this lane, and every lane gets a home AND a scratch — the "
        f"chooser is reading the wrong thing:\n{argv}")
    return sorted(set(out))


def under(path, root):
    path, root = str(path).rstrip("/"), str(root).rstrip("/")
    return path == root or path.startswith(root + "/")


def a_root_no_lane_can_write(candidate, invented):
    """`candidate`, once it is shown to be outside every path the policy invents for a lane.

    The chooser this criterion asks for. A candidate inside the writable set is refused HERE,
    naming the entry it collides with, rather than being handed to `git worktree add` and
    producing a lane inside a lane whose failure is about neither.
    """
    for fresh in invented:
        if under(candidate, fresh):
            raise AssertionError(
                f"{candidate} is under {fresh!r}, which this lane's policy mounts FRESH inside "
                f"the sandbox and binds back from nowhere — a repository built there is not "
                f"there for the lane, and cutting a worktree into it is a lane inside a lane "
                f"(`SRV-FIX-460`)")
    return str(candidate)


def test_a_fixture_repository_can_host_a_lane_wherever_the_suite_runs(tmp_path, serve,
                                                                      argv_recorder):
    """§1 E2 — the root the suite builds a repository at is outside every writable path a lane gets.

    `SRV-FIX-460`. Asserted of the root pytest actually hands this case — `tmp_path` — so it is a
    fact about where the suite runs and not about a directory somebody chose once, and asked of
    the POLICY the tool built for a real lane rather than of a constant.

    Then the property is driven rather than described: a repository built there really does host a
    lane, and the lane's checkout really does land outside every writable entry.
    """
    from test_lane import build_repo, claim

    probe = build_repo(tmp_path / "probe")
    probing = serve(probe, FACTORY_TEST_HOOKS="1", FACTORY_CONFINE_BIN=argv_recorder.bin)
    first = claim(probing, ticket="TCK-001")
    assert first.status_code == 201, f"the probe lane was refused: {first.text[:400]}"
    ran = probing.post("/api/_test/lane-process",
                       json={"lane": first.json()["lane"], "command": "true"})
    assert ran.status_code == 202, ran.text
    argv = argv_recorder.argv()
    serve.stop(probing)

    invented = invented_for_a_lane(argv)

    root = a_root_no_lane_can_write(tmp_path / "fixture", invented)
    build_repo(root)
    srv = serve(root)

    lane = claim(srv, ticket="TCK-001")
    assert lane.status_code == 201, (
        f"a repository the suite built at {root} could not host a lane: "
        f"{lane.status_code} {lane.text[:400]}")
    worktree = lane.json()["worktree"]
    for path in invented:
        assert not under(worktree, path), (
            f"the lane's checkout landed at {worktree}, under {path!r} — the suite is running "
            f"inside a lane and this case is measuring two sandboxes")


def test_a_fixture_root_under_the_lane_scratch_is_refused_with_the_reason(tmp_path):
    """§1 E-NEG — a root inside the writable set is refused by the chooser, naming the entry.

    MANDATORY NEGATIVE, and it is the half that makes the case above worth anything: without it,
    `a_root_no_lane_can_write` could return its argument unread and both would pass.

    Every member of a policy's writable set is driven, and the sweep is asserted non-empty before
    anything is concluded from it. The policy here is a written-down one rather than a live lane's,
    because what is under test is the CHOOSER and a live lane would make this case depend on the
    machine it runs on.
    """
    invented = ["/home/lane2", "/tmp/factory2"]
    assert invented, "the chooser has nothing to avoid"

    for path in invented:
        for candidate in (path, f"{path}/repo", f"{path}/deeper/still/repo"):
            with pytest.raises(AssertionError) as why:
                a_root_no_lane_can_write(candidate, invented)
            assert path in str(why.value), (
                f"{candidate} was refused without naming the entry it collides with: {why.value}")

    # ...and a root outside every one of them is returned unchanged, so the chooser is not
    # simply refusing everything.
    outside = tmp_path / "elsewhere"
    assert a_root_no_lane_can_write(outside, invented) == str(outside)
