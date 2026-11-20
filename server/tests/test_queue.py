"""`core::Queue::offload` carries the worker's result back as the completion's parameter.

Before SRV-FIX-476 the worker returned nothing and its completion landed on the raw engine, so
every caller in `app/` allocated a `std::shared_ptr` before the hop and re-posted onto its own
queue by hand — 30 two-argument `core::Worker::instance().post(` calls, 57 result slots, and
13 hand-written re-hops. `server/design/filesystem-refactor-workflow.md:156-158` already claimed
*"after conversion the wrong version does not compile"*; it was false for `app/lane` and for all
19 of `app/api`.

These cases are about the TYPE, not about a grep: the compiler is the thing that has to refuse.
"""

from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]


VALUE_AND_VOID = """#include <string>

#include <core/queue.h>

int useTheValue(core::Queue& queue)
{
    static int seen = 0;
    queue.offload([] { return 42; }, [](int v) { seen = v; });
    queue.offload([] { return std::string("text"); }, [](std::string v) { seen = int(v.size()); });
    queue.offload([] {}, [] { seen = 1; });
    return seen;
}
"""


def test_a_completion_receives_the_workers_result_on_the_queue():
    """§1 criterion 1.

    A component that moves work off its queue writes the result's arrival as the completion's
    parameter. Both kinds are named and compiled in the same run — a `work` that produces a value
    and a `work` that produces none — because a template that only ever saw one of them is a
    template nobody proved the other half of.

    Compiled against the real headers with the real flags out of the build's own
    `compile_commands.json`, so this is a fact about what the tree accepts.
    """
    from test_conventions import compile_probe

    code, err = compile_probe(VALUE_AND_VOID)
    assert code == 0, (
        f"core::Queue::offload does not carry a worker's result to its completion, so every "
        f"caller in app/ still has to allocate a result slot before the hop:\n{err}")


OFF_THE_QUEUE = """#include <core/worker.h>

void hop()
{
    core::Worker::instance().post([] {}, [] {});
}
"""

ON_THE_QUEUE = """#include <core/queue.h>

void hop(core::Queue& queue)
{
    queue.offload([] {}, [] {});
}
"""

MISMATCHED = """#include <string>

#include <core/queue.h>

void hop(core::Queue& queue)
{
    queue.offload([] { return 42; }, [](std::string) {});
}
"""

MATCHED = """#include <string>

#include <core/queue.h>

void hop(core::Queue& queue)
{
    queue.offload([] { return std::string("text"); }, [](std::string) {});
}
"""


def test_a_completion_off_the_queue_has_no_type():
    """§1 criterion 2 · MANDATORY NEGATIVE.

    `manual_worker_hop` was supposed to own this shape and could never fire: it keys on
    `boost::asio::post(` appearing near a `Worker::instance().post(`, and `app_names_asio` exists
    to guarantee that string is absent from `app/`. A rule whose precondition another rule
    eliminates reports zero on every input, and `--list` prints a dead rule and a clean tree
    identically. So the refusal is moved to where it cannot retire itself: the two-argument
    `core::Worker::post` is private to `core::Queue`, and the hand-written hop stops having a
    type.

    The CONTROL is the half that makes the refusal mean something — the same unit calling
    `queue.offload([] {}, [] {})` must compile clean in the same run, so a missing header or a
    stale build cannot make this case pass. The second probe is the same question one level in:
    a `work` returning `int` with a `done` taking `std::string` is refused, and the matching pair
    is accepted alongside it.
    """
    from test_conventions import compile_probe

    code, err = compile_probe(OFF_THE_QUEUE)
    assert code != 0, (
        "core::Worker::instance().post(task, callback) still compiles from a translation unit "
        "that is not core::Queue, so a completion can still land on the raw engine while the "
        "caller's state lives on a strand — the shape SRV-FIX-476 exists to make unwritable")

    code, err = compile_probe(ON_THE_QUEUE)
    assert code == 0, (
        f"the CONTROL probe did not compile, so the refusal above proves nothing about the "
        f"two-argument Worker::post:\n{err}")

    code, err = compile_probe(MISMATCHED)
    assert code != 0, (
        "queue.offload accepted a `work` returning int beside a `done` taking std::string, so "
        "the result is not the completion's parameter — it is whatever the caller remembered to "
        "put in a slot")

    code, err = compile_probe(MATCHED)
    assert code == 0, (
        f"the CONTROL probe for the matching pair did not compile, so the refusal above is not "
        f"about the mismatch:\n{err}")
