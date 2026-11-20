"""SRV-FIX-030 — what the REAL agent CLI does with the bytes this tool writes to a run's stdin.

`SRV-FEAT-009` proved its steering against a stand-in that read plain lines from stdin, because
the test wrote the stand-in to do that. The tool wrote plain lines for the same reason. Both
halves agreed, nothing checked either against the CLI, and against `claude 2.1.92` the default
input format reads stdin **to EOF as one prompt** — so one message per line is one turn for a
whole session (`docs/operations/ESCAPES.md`, the row naming *a stand-in written from the same assumption as
the code under test*).

This module is the arbiter that was missing. It drives the real CLI through **the same stdin
channel `agent::Store` uses** — a named FIFO, created before the fork, held open by the parent for
the life of the run, one message written per line and spaced in time — and records exactly what
came back. It is the script that produced every `fixtures/cli-stdin-*.json` capture, so a fixture
here is never something somebody wrote down; it is something the CLI did, re-runnable by anybody.

Two consumers, and they are the point:

- `test_agent.py::test_the_real_cli_takes_the_bytes_this_tool_writes` replays a live run's own
  stdin bytes into the real CLI, so `E14`/`E15` are checked against the dependency and not
  against a script that echoes what it is given.
- `test_agent.py::test_the_stand_in_agrees_with_the_captured_cli` runs the **stand-in** over the
  same stdin lines and requires the same verdict. A stand-in that accepts a line the CLI rejects
  fails there, which is the whole class of defect this ticket exists to close.
"""

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE_DATE = "2026-07-28"

CLAUDE = shutil.which("claude")
REAL_CLI_OFF = os.environ.get("FACTORY_REAL_CLI") == "0"
NO_REAL_CLI = (
    "the real agent CLI is not on PATH — this criterion is a contract test against the "
    "dependency and there is nothing to contract with"
    if not CLAUDE else
    "FACTORY_REAL_CLI=0 — the contract test against the real CLI was switched off deliberately"
    if REAL_CLI_OFF else None)


def user_frame(text):
    """One message, framed as `--input-format stream-json` reads it. One object, one line."""
    return json.dumps({"type": "user",
                       "message": {"role": "user",
                                   "content": [{"type": "text", "text": text}]}},
                      ensure_ascii=False)


def drive(argv_tail, stdin_lines, gap=6.0, timeout=240):
    """Run the real CLI over a FIFO the way a run's stdin is run, and report what happened.

    `argv_tail` is everything after the command word — in the live test it is the argv the TOOL
    built, lifted out of the child's own log, so the flags under test are never retyped here.
    """
    if CLAUDE is None:
        raise RuntimeError("the real agent CLI is not on PATH")

    workdir = tempfile.mkdtemp(prefix="har030-cli-")
    fifo = os.path.join(workdir, "stdin")
    os.mkfifo(fifo)
    # O_NONBLOCK so the open cannot block on there being no writer, then cleared before the fd
    # becomes the child's fd 0 — SRV-FEAT-009 §1.9, and it cost that ticket a spike.
    rfd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    os.set_blocking(rfd, True)
    wfd = os.open(fifo, os.O_WRONLY)

    argv = [CLAUDE] + list(argv_tail)
    started = time.time()
    proc = subprocess.Popen(argv, stdin=rfd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            cwd=workdir, text=True)
    os.close(rfd)

    def feed():
        for i, line in enumerate(stdin_lines):
            if i:
                time.sleep(gap)          # spaced, so "one turn each" is a claim with teeth
            try:
                os.write(wfd, (line + "\n").encode())
            except OSError:
                break
        time.sleep(gap)
        try:
            os.close(wfd)                # the last writer closing is what ends the session
        except OSError:
            pass

    pump = threading.Thread(target=feed, daemon=True)
    pump.start()
    try:
        out, err = proc.communicate(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        timed_out = True
    pump.join(timeout=gap + 5)

    frames = []
    unparseable = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            frames.append(json.loads(line))
        except ValueError:
            unparseable.append(line[:200])

    results = [f for f in frames if f.get("type") == "result"]
    users = [f for f in frames if f.get("type") == "user"]
    return {
        "cli_version": subprocess.run([CLAUDE, "--version"], capture_output=True,
                                      text=True).stdout.strip(),
        "captured": CAPTURE_DATE,
        "argv_tail": list(argv_tail),
        "stdin": list(stdin_lines),
        "rc": proc.returncode,
        "timed_out": timed_out,
        "took_s": round(time.time() - started, 1),
        "stderr": err.strip()[:2000],
        "frame_types": sorted({f.get("type") for f in frames}),
        "frames": len(frames),
        "unparseable": unparseable,
        "results": [r.get("result") for r in results],
        "user_frames": [u.get("message", {}).get("content", [{}])[0].get("text") for u in users],
        "stdout": out,
    }


def capture_path(name):
    return FIXTURES / f"cli-stdin-{CAPTURE_DATE}-{name}.json"


def load(name):
    """A committed capture. The arbiter — never a frame anybody invented."""
    return json.loads(capture_path(name).read_text())


def record(name, argv_tail, stdin_lines, **kw):
    """Capture once and commit it. `python3 tests/cli_conformance.py` re-makes all of them."""
    data = drive(argv_tail, stdin_lines, **kw)
    stdout = data.pop("stdout")
    FIXTURES.mkdir(exist_ok=True)
    capture_path(name).write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    (FIXTURES / f"cli-stdin-{CAPTURE_DATE}-{name}.stdout.jsonl").write_text(stdout)
    print(f"{name:28s} rc={data['rc']:<3} results={len(data['results'])} "
          f"users={len(data['user_frames'])} frames={data['frames']} "
          f"{data['stderr'][:70]}")
    return data


# ── the six questions the design could not be written without ────────────────────────────────
#
# Every one of them was measured before a line of the fix existed, and each committed capture is
# the answer. `two-messages` is the pair that matters: same two messages, same channel, and the
# only difference is the input format.

STREAM = ["--print", "--input-format", "stream-json", "--output-format", "stream-json",
          "--verbose"]
DEFAULT_INPUT = ["--print", "--output-format", "stream-json", "--verbose"]
TWO = ["say only the word one", "say only the word two"]
NASTY = 'reply with exactly this and nothing else: he said "hi"\\done'


def main():
    # 0 · SRV-FEAT-009's steering, byte for byte, against the real CLI. The argv and the three
    #     stdin lines are what the tool produced before this fix — lifted out of the RED run's
    #     own child log, not retyped — and `E14`+`E15`+`E14`+`E14` come back as ONE turn.
    record("srv-feat-009-steering-collapses",
           ["--output-format", "stream-json", "--verbose",
            "--session-id", "d2fb28ff-c39a-499e-a149-5a66c185667b"],
           ["say only the word one", "say only the word two", "say only the word three"])
    # 1 · THE DEFECT. What SRV-FEAT-009 writes today, into the argv SRV-FEAT-010 ships today.
    record("default-input-two-messages", DEFAULT_INPUT, TWO)
    # 2 · THE FIX. The same two messages, framed, one turn each.
    record("stream-json-two-messages", STREAM, [user_frame(t) for t in TWO])
    # 3 · A prompt in the argv is SILENTLY DROPPED once the input is framed.
    record("argv-prompt-is-dropped", STREAM + ["-p", "say only the word zero"],
           [user_frame("say only the word one")])
    # 4 · A line that is not JSON does not garble a message. It ENDS THE RUN.
    record("a-line-that-is-not-json", STREAM,
           ["say only the word one", user_frame("say only the word two")])
    # 5 · A quote, a newline and a backslash survive the framing, end to end.
    record("quote-newline-backslash", STREAM, [user_frame(NASTY)])
    # 6 · --replay-user-messages echoes the human's own text back as a `user` frame, which this
    #     tool's parser renders as `prose` — the same message twice. That is why it is not passed.
    record("replay-user-messages", STREAM + ["--replay-user-messages"],
           [user_frame(t) for t in TWO])


if __name__ == "__main__":
    main()
