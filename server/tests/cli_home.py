"""SRV-FIX-052 — what the real agent CLI WRITES into its configuration directory, measured.

`SRV-FEAT-039` pointed `CLAUDE_CONFIG_DIR` at a directory and bound it read-only. Two of the three
things that variable moves are reads — the credentials and the trust — and the third is not: the
CLI creates a per-session scratch directory under it before it can start a shell, so a read-only
configuration directory is an agent that answers and cannot run a command.

`docs/tickets/_TEMPLATE.md` §2 — *A STAND-IN IS JUDGED AGAINST THE REAL THING* — applies: the
subject of this ticket is an external program's use of its own configuration directory, so what
it writes there is measured against the program and committed, never reasoned about.

Two cells, one real turn each. Both ask for the same one-word shell command; they differ only in
whether the configuration directory can be written.

Re-capture, from the repository root:

    cd factory/server && python3 tests/cli_home.py

That overwrites `tests/fixtures/cli-home-<date>-*.json`. `tests/test_lane_home.py` and
`tests/test_agent_trust.py` read them and never run the CLI for the facts they carry.
"""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE_DATE = "2026-07-30"
PREFIX = f"cli-home-{CAPTURE_DATE}-"

CLAUDE = shutil.which("claude")
ARGV_TAIL = ["--print", "--input-format", "stream-json",
             "--output-format", "stream-json", "--verbose"]


def tree(root):
    root = Path(root)
    if not root.is_dir():
        return []
    return sorted(str(p.relative_to(root)) + ("/" if p.is_dir() else "")
                  for p in root.rglob("*"))


def run_cell(name, note, writable):
    """One real turn, in a workspace of its own, against a configuration directory the TOOL built.

    The credential is reached by hard link — the same carriage the product now performs — so the
    capture also records in passing that a lane needs nothing of the developer's directory but
    that one file.
    """
    if CLAUDE is None:
        raise RuntimeError("the real agent CLI is not on PATH")

    root = Path(tempfile.mkdtemp(prefix="har052-home-"))
    ws = root / "workspace"
    ws.mkdir()
    nonce = "har052-cli-" + os.urandom(5).hex()
    (ws / ".claude").mkdir()
    (ws / ".claude" / "settings.json").write_text(json.dumps(
        {"permissions": {"allow": [f"Bash(echo {nonce})"]}}, indent=2))

    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(ws), check=True, timeout=60)

    home = root / "home"
    home.mkdir()
    cfg = root / "config"
    cfg.mkdir(mode=0o700)
    # Exactly what the product seeds: the generated trust, and the credential by hard link.
    (cfg / ".claude.json").write_text(json.dumps(
        {"projects": {str(Path(os.path.realpath(ws))): {"hasTrustDialogAccepted": True}}}))
    token = Path(os.path.expanduser("~")) / ".claude" / ".credentials.json"
    try:
        os.link(token, cfg / ".credentials.json")
        carriage = "hard link"
    except OSError:
        shutil.copy2(token, cfg / ".credentials.json")
        os.chmod(cfg / ".credentials.json", 0o600)
        carriage = "copy 0600"

    before = tree(cfg)
    if not writable:
        os.chmod(cfg, 0o500)

    prompt = (f"Use the Bash tool to run exactly this command: echo {nonce}\n"
              f"Then reply with the one line it printed, or with the error if it failed.")
    frame = json.dumps({"type": "user",
                        "message": {"role": "user",
                                    "content": [{"type": "text", "text": prompt}]}})

    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CONFIG_DIR"}
    env["HOME"] = str(home)
    env["CLAUDE_CONFIG_DIR"] = str(cfg)
    proc = subprocess.run([CLAUDE] + ARGV_TAIL, input=frame + "\n", cwd=str(ws), env=env,
                          capture_output=True, text=True, timeout=600)

    if not writable:
        os.chmod(cfg, 0o700)
    after = tree(cfg)

    frames = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            frames.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    result = next((f for f in frames if f.get("type") == "result"), {})

    record = {
        "capture": name,
        "date": CAPTURE_DATE,
        "note": note,
        "cli_version": subprocess.run([CLAUDE, "--version"], capture_output=True,
                                      text=True, timeout=60).stdout.strip(),
        "argv": [CLAUDE] + ARGV_TAIL,
        "cwd": str(ws),
        "env": {"HOME": str(home), "CLAUDE_CONFIG_DIR": str(cfg)},
        "config_dir_was_writable": writable,
        "credential_carriage": carriage,
        "workspace_settings": json.loads((ws / ".claude" / "settings.json").read_text()),
        "nonce": nonce,
        "rc": proc.returncode,
        "the_agent_authenticated": any(f.get("type") == "system" for f in frames),
        "config_dir_before": before,
        "config_dir_after": after,
        "the_cli_created": [p for p in after if p not in before],
        "the_command_ran": any(
            nonce in json.dumps(c.get("content", ""))
            for f in frames if f.get("type") == "user"
            for c in [f.get("message", {})]),
        "permission_denials": result.get("permission_denials", []),
        "result_text": result.get("result"),
        "stderr": proc.stderr,
        "ignoring_lines": [l for l in proc.stderr.splitlines() if l.startswith("Ignoring ")],
        "stdout": proc.stdout,
    }
    FIXTURES.mkdir(exist_ok=True)
    (FIXTURES / f"{PREFIX}{name}.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    shutil.rmtree(root, ignore_errors=True)
    return record


def capture_all():
    return {
        "a-writable-config-directory-runs-a-command": run_cell(
            "a-writable-config-directory-runs-a-command",
            "THE MEASUREMENT. The configuration directory is the tool's and it is writable: the "
            "CLI creates what it needs under it and the declared Bash rule runs. What it created "
            "is `the_cli_created`, and that list is why a read-only bind cannot work.",
            writable=True),
        "a-read-only-config-directory-cannot-run-one": run_cell(
            "a-read-only-config-directory-cannot-run-one",
            "The same everything, with the directory not writable. The agent still authenticates "
            "and still answers — which is what made this defect invisible for a day — and the "
            "command does not run. SRV-FEAT-039's bind produced EROFS here; a mode of 0500 "
            "produces EACCES, and the requirement they demonstrate is the same one: the CLI "
            "WRITES its configuration directory before it can start a shell.",
            writable=False),
    }


if __name__ == "__main__":
    for name, rec in capture_all().items():
        print(f"{name}: rc={rec['rc']} authenticated={rec['the_agent_authenticated']} "
              f"created={rec['the_cli_created']} denials={len(rec['permission_denials'])}")
        print("   result:", (rec["result_text"] or "")[:300].replace("\n", " "))
