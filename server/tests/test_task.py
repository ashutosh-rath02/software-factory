"""`core::Task` runs on the queue it was given, and there is no other one to find.

`core::Task`'s constructor takes a `core::Queue&`. Before SRV-FIX-443 it took a `Component*` and
built its timer on `core::Component`'s own `queue_` — a strand no component posts a handler onto,
because every one of them keeps its state on a queue of its own. Two strands serialise nothing
between them, so `lane::Store`'s sweep, silence and settle timers mutated `lanes_`, `silence_`
and `settling_` beside every handler that touches them.

The two cases that used to live here asserted the string `strandOf(c->queue())` in `core/task.cpp`
and the presence of `Queue& queue()` in `core/component.h`. Both were green throughout, because
*the component's queue* had two referents. They are deleted rather than repaired: a source-reading
proxy that passed the defect it was written for is not evidence, and SRV-FIX-443 §1 says so in as
many words. `test_a_declared_task_with_no_handler_does_not_link` goes with them — it is about
`M_TASK`, and `M_TASK` is one of the things this ticket deletes: a macro cannot name a queue.

What replaces all three is the COMPILER. The wrong construction no longer has a type.
"""

import subprocess
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]


def test_a_task_cannot_be_built_without_a_queue():
    """§1 criterion 4 · MANDATORY NEGATIVE, and it is the compiler that says no.

    `core::Task(Component*, …)` could only find one executor — `core::Component`'s own `queue_`,
    which no component posts a handler onto — so every timer in `app/` ran beside the state it
    mutated. The fix is not a rule anybody has to remember: `Task` takes the queue, `Component`
    has none, and the construction that produced the defect no longer has a type.

    Compiled against the real headers with the real flags, taken from the build's own
    `compile_commands.json`, so this is a fact about what the tree accepts and not about a grep.
    The CONTROL probe is what makes the refusal mean something: the same call with a
    `core::Queue&` must compile clean in the same run, so a broken probe, a missing header or a
    stale build cannot make this case pass.
    """
    from test_conventions import compile_probe

    wrong = """#include <core/component.h>
#include <core/task.h>

class Probe : public core::Component {};

core::Task build(Probe* p)
{
    return core::Task {p, [] {}};
}
"""
    right = """#include <core/queue.h>
#include <core/task.h>

core::Task build(core::Queue& queue)
{
    return core::Task {queue, [] {}};
}
"""
    code, err = compile_probe(wrong)
    assert code != 0, (
        "core::Task was built from a Component* alone, so a timer can still be bound to an "
        "executor nothing else uses — the defect SRV-FIX-443 exists to make unwritable")

    code, err = compile_probe(right)
    assert code == 0, (
        f"the CONTROL probe did not compile, so the refusal above proves nothing about the "
        f"constructor:\n{err}")



def test_a_task_may_destroy_itself_inside_its_own_handler():
    """The one case in this file that RUNS the code instead of reading it.

    `lane::Store`'s silence handler does `silence_.erase(name)` — a task destroying the map entry
    that owns it, from inside its own handler. If `Impl` is held by `unique_ptr` and the timer's
    completion captured a raw `Impl*`, that frees `Impl` under the running lambda and the server
    segfaults; the suite reports it as `Connection refused`, which reads like a broken server
    rather than a broken lifetime.

    This has now happened TWICE in one day — once in `core::Timer`, and again in `core::Task` when
    it was rewritten to the `Task(Component*, Handler)` API and the `shared_from_this` self-capture
    was reproduced away. The source-reading cases this file used to carry could not see either:
    they asserted which EXECUTOR the timer is on by reading `task.cpp`, and a lifetime bug is
    invisible to that.

    SRV-FIX-443 keeps it and re-points it at the new constructor, because the lifetime became
    load-bearing in one more place: `lane::Store::kill_` arms a task at `killing_[pid]` from
    inside the handler of the task that already lives there, every 20 ms for the whole grace.
    """
    src = SERVER / "tests" / "fixtures" / "task-self-destroy.cpp"
    src.parent.mkdir(exist_ok=True)
    src.write_text('''
#include <core/engine.h>
#include <core/task.h>
#include <core/worker.h>

#include <cstdio>
#include <unistd.h>
#include <map>
#include <memory>
#include <string>

class Probe : public core::Component {
public:
    static Probe& instance() { static Probe p; return p; }
    core::Queue& queue() { return queue_; }
    core::Queue queue_;
    std::map<std::string, core::Task> tasks;
    int fired = 0;

    void arm(const std::string& name)
    {
        tasks.insert_or_assign(name, core::Task {queue_, [this, name] {
            ++fired;
            tasks.erase(name);          // destroys the Task from inside its own handler
        }});
        tasks.at(name).start(5, 1);
    }
};

int main()
{
    auto& engine = core::Engine::instance();
    engine.registerComponent<core::Worker>();
    engine.registerComponent<Probe>();

    Probe::instance().queue().post([] {
        for (int i = 0; i < 32; ++i) Probe::instance().arm("lane-" + std::to_string(i));
    });

    core::Task quit {Probe::instance().queue(), [] {
        std::printf("fired=%d\\n", Probe::instance().fired);
        std::fflush(stdout);\n        _exit(Probe::instance().fired == 32 ? 0 : 3);
    }};
    quit.start(1500, 1);

    engine.start();
    return 4;
}
''')
    binary = SERVER.parent / ".cache" / "server" / "build" / "task-self-destroy"
    binary.parent.mkdir(parents=True, exist_ok=True)
    try:
        build = subprocess.run(
            ["c++", "-std=c++17", "-fsanitize=address", "-g", f"-I{SERVER}", str(src),
             str(SERVER / "core" / "task.cpp"), str(SERVER / "core" / "queue.cpp"),
             str(SERVER / "core" / "engine.cpp"), str(SERVER / "core" / "worker.cpp"),
             "-o", str(binary), "-lglog", "-lboost_system", "-lpthread"],
            capture_output=True, text=True, timeout=300)
        assert build.returncode == 0, f"the fixture did not build:\n{build.stderr[-1500:]}"

        r = subprocess.run([str(binary)], capture_output=True, text=True, timeout=90,
                           env={**__import__("os").environ, "ENGINE_THREADS": "8"})
        assert "heap-use-after-free" not in r.stderr, (
            "AddressSanitizer caught a use-after-free: a task was destroyed inside its own "
            "handler and its Impl was freed under the running completion.\n"
            "core::Task::Impl must outlive its own handler — hold it by shared_ptr and capture "
            "shared_from_this() in the async_wait completion.\n"
            + r.stderr[:1800])
        assert r.returncode == 0, (
            f"32 tasks each erased their own map entry from inside their handler and the process "
            f"exited {r.returncode} (-11 is SIGSEGV).\n"
            f"core::Task::Impl must outlive its own handler — hold it by shared_ptr and capture "
            f"shared_from_this() in the async_wait completion.\n{r.stdout}{r.stderr[-800:]}")
        assert "fired=32" in r.stdout, r.stdout
    finally:
        src.unlink(missing_ok=True)
        binary.unlink(missing_ok=True)
