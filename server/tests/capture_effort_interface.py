"""SRV-FEAT-574 — record what the two installed providers really do with a reasoning effort.

The stand-ins in `test_agent_effort.py` are judged against the captures this script writes, so a
stand-in that refuses an effort the real program accepts — or accepts one the real program drops —
fails there rather than agreeing with the code it was written beside.

Two facts are recorded, and the second is the one that decides where the refusal has to live:

  claude  `--effort <level>` is a real flag, and an UNDECLARED value is NOT refused: the CLI
          prints `Warning: Unknown --effort value '<v>' — ignoring it and using the default
          effort.` on stderr, exits 0, and runs at the default. So "no silent drop to the
          default" is a property only the tool can hold, at E18, before the spawn.

  codex   `TurnStartParams.effort` exists and `ThreadStartParams` has none, so the effort rides
          `turn/start` and never `thread/start`. `ReasoningEffort` is a non-empty string with no
          enum, which is why the level names are the manifest's and never the tool's.

Re-capture both, from a checkout with the two CLIs on PATH:

    python3 server/tests/capture_effort_interface.py --out server/tests/fixtures
"""

import argparse
import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

DATE = "2026-10-10"
CLAUDE_FIXTURE = f"cli-effort-{DATE}-claude-drops-an-undeclared-effort.json"
CODEX_FIXTURE = f"cli-effort-{DATE}-codex-turn-start-takes-an-effort.json"


def _run(argv, **kwargs):
    done = subprocess.run(argv, capture_output=True, text=True, timeout=180, **kwargs)
    return {"argv": list(argv), "exit": done.returncode,
            "stdout": done.stdout, "stderr": done.stderr}


def capture_claude(binary):
    """What the installed `claude` does with the flag, and with a value nobody declared."""
    version = _run([binary, "--version"])
    help_text = _run([binary, "--help"])
    flag_lines = [line.strip() for line in help_text["stdout"].splitlines()
                  if "--effort" in line]
    probes = {
        "declared value": _run([binary, "--effort", "high", "--version"]),
        "undeclared value": _run([binary, "--effort", "bogus", "--version"]),
        "empty value": _run([binary, "--effort", "", "--version"]),
    }
    return {
        "captured": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "cli_version": version["stdout"].strip(),
        "program": binary,
        "purpose": "what the real claude CLI accepts as --effort, and what it does with a value "
                   "the manifest never declared; no repository content and no model call",
        "help_lines": flag_lines,
        "flag": "--effort",
        "probes": probes,
        "refuses_an_undeclared_value": any(
            probe["exit"] != 0 for name, probe in probes.items() if name != "declared value"),
        "warns_and_defaults_instead": all(
            "Unknown --effort value" in probe["stderr"]
            for name, probe in probes.items() if name == "undeclared value"),
        # DERIVED, never a constant: a capture that did not actually reach the CLI must say so.
        # Written `True` unconditionally it is a flag that can only ever agree with itself, and a
        # case asserting it then evidences nothing about the capture it guards.
        "fixture_verified": bool(flag_lines) and bool(version["stdout"].strip())
                            and all(probe["stderr"] is not None for probe in probes.values()),
    }


def capture_codex(binary):
    """Where the installed app-server takes an effort, and where it does not."""
    version = _run([binary, "--version"])
    out = Path(tempfile.mkdtemp(prefix="srv-feat-574-codex-"))
    generated = _run([binary, "app-server", "generate-json-schema", "--out", str(out)])
    found = {}
    for name in ("TurnStartParams", "ThreadStartParams", "ThreadResumeParams"):
        matches = sorted(out.rglob(name + ".json"))
        if not matches:
            found[name] = None
            continue
        schema = json.loads(matches[0].read_text())
        effort = schema.get("properties", {}).get("effort")
        found[name] = {
            "path": str(matches[0].relative_to(out)),
            "has_effort": effort is not None,
            "effort": effort,
            "reasoning_effort": schema.get("definitions", {}).get("ReasoningEffort"),
        }
    shutil.rmtree(out, ignore_errors=True)
    return {
        "captured": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "cli_version": version["stdout"].strip(),
        "program": binary,
        "purpose": "where the installed codex app-server protocol carries a reasoning effort; "
                   "a schema dump only, no repository content and no model call",
        "generate_exit": generated["exit"],
        "params": found,
        "fixture_verified": generated["exit"] == 0 and bool(version["stdout"].strip())
                            and any(body and body["has_effort"] for body in found.values())
                            and any(body and not body["has_effort"] for body in found.values()),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent / "fixtures"))
    parser.add_argument("--claude", default=shutil.which("claude"))
    parser.add_argument("--codex", default=shutil.which("codex"))
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if not args.claude or not args.codex:
        raise SystemExit("both `claude` and `codex` must be on PATH to re-capture")

    for name, record in ((CLAUDE_FIXTURE, capture_claude(args.claude)),
                         (CODEX_FIXTURE, capture_codex(args.codex))):
        (out / name).write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
        print("wrote", out / name)


if __name__ == "__main__":
    main()
