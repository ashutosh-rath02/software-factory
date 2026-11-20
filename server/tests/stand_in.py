"""The one place a stand-in agent learns to read a run's stdin — and it learns it from a capture.

`docs/operations/ESCAPES.md`: `SRV-FEAT-009`'s stand-in read plain lines because the test wrote it to, the
tool wrote plain lines for the same reason, and nothing checked either against the CLI. The
stand-in was written from the same assumption as the code, so eight green criteria proved the
tool's half of a contract whose other half had never been looked at.

So the reader below is defined ONCE, both stand-ins use it, and
`test_agent.py::test_the_stand_in_agrees_with_the_captured_cli` runs it over the committed
`fixtures/cli-stdin-*.json` captures and requires the same verdict the real CLI gave — accepted
where it accepted, and dead where it died. A stand-in that is more forgiving than the dependency
is a stand-in that hides the next defect of this shape.

Two behaviours are copied from `claude 2.1.92` because they were measured, not assumed:

- one message is ONE line of JSON, `{"type":"user","message":{"role":"user","content":[{"type":
  "text","text":…}]}}` — `cli-stdin-2026-07-28-stream-json-two-messages.json`;
- a line that will not parse does not garble a message, it **ends the run**: rc 1, no frames,
  `Error parsing streaming input line` — `cli-stdin-2026-07-28-a-line-that-is-not-json.json`.
"""

# `$log` must already be set. Sets `line` to the raw bytes read and `text` to the message, and
# returns 1 at end of input so the caller can still tell EOF from a trapped signal.
#
# The decode runs in a helper process because a POSIX shell cannot parse JSON, and a shell that
# *approximately* parses JSON is a stand-in agreeing with whatever this repository believes —
# which is the defect. Two consequences of that helper are handled explicitly, because the real
# CLI parses stdin inside its own single process and has neither:
#
#   · the line is handed over as an ARGUMENT, not down a pipe, so `E15`'s group-wide SIGINT
#     cannot shoot a left-hand subshell mid-write and leave the decoder reading nothing;
#   · exit 3 — and only exit 3 — means *these bytes are not a message*. Any other failure is the
#     helper being signalled, which is not a framing error and is retried. Without that, an
#     interrupt landing in the ~15 ms of interpreter start-up before `SIG_IGN` is installed reads
#     as a message the CLI would have refused, and the run dies for a reason nothing did.
# SRV-FIX-055. Does the agent's own filesystem contain what the prompt just named?
#
# `$text` must be set — call it straight after `read_frame`. Every whitespace-separated word that
# looks like an absolute path to a `.md` file is tried, and the verdict goes in the log as
# `opened:<path>` or `unreadable:<path>`. One trailing `.` or `,` is stripped, because a prompt is
# a sentence and a path at the end of one carries the sentence's punctuation.
#
# It reads no prompt format. Both stand-ins say the path differently — `Run ticket <p>.` and
# `The ticket is at <p>, in …` — and a probe that learned either one would be a probe that has
# learned this suite's wording rather than the question. The question is whether the file is
# THERE, and the only honest way to ask is to open it: a lane's agent lives in a mount namespace
# that binds its worktree and not the main checkout, so a path from the tool's own filesystem
# comes back unreadable here for exactly the reason it does in a real run.
PATH_PROBE = r"""
probe_paths() {
  for word in $text ; do
    case "$word" in
      /*)
        candidate=`printf '%s' "$word" | sed 's/[.,]*$//'`
        case "$candidate" in
          *.md)
            if [ -r "$candidate" ] ; then
              printf '%s\n' "opened:$candidate" >> "$log"
            else
              printf '%s\n' "unreadable:$candidate" >> "$log"
            fi ;;
        esac ;;
    esac
  done
}
"""

FRAME_READER = r"""
read_frame() {
  read -r line || return 1
  printf '%s\n' "raw:$line" >> "$log"
  attempt=0
  while : ; do
    text=`/usr/bin/python3 -c '
import json, signal, sys
signal.signal(signal.SIGINT, signal.SIG_IGN)
try:
    frame = json.loads(sys.argv[1])
    assert frame.get("type") == "user"
    decoded = frame["message"]["content"][0]["text"]
except KeyboardInterrupt:
    raise SystemExit(4)
except Exception:
    raise SystemExit(3)
sys.stdout.write(decoded)
' "$line"`
    status=$?
    if [ "$status" -eq 0 ] ; then return 0 ; fi
    if [ "$status" -eq 3 ] ; then break ; fi
    attempt=`expr $attempt + 1`
    if [ "$attempt" -ge 3 ] ; then break ; fi
  done
  printf '%s\n' "Error parsing streaming input line: $line" >&2
  printf '%s\n' "input-error" >> "$log"
  exit 1
}
"""
