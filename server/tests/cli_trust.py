"""SRV-FIX-047 — where the REAL agent CLI reads its trust store from, measured.

`SRV-FEAT-039` pointed the agent at `$CLAUDE_CONFIG_DIR` to fix `Not logged in`. It fixed that.
It also moved the CLI's *trust store*, and nothing looked: the developer's trust lives in
`$HOME/.claude.json`, the agent now reads `$CLAUDE_CONFIG_DIR/.claude.json`, and a repository's
107 declared permissions were dropped on every run (`docs/operations/ESCAPES.md`).

`docs/tickets/_TEMPLATE.md` §2 — *A STAND-IN IS JUDGED AGAINST THE REAL THING* — applies: the
subject of that ticket is an external program's behaviour, so it is measured against the program
and committed, never reasoned about.

This module is that measurement. It drives the real `claude` binary once per cell and records
argv, environment, cwd, rc, stdout, stderr and whether the config file was modified. Every cell
costs **no API tokens**: the CLI emits its trust warnings while loading settings, before it reads
stdin, so a deliberately unparseable stdin line makes it print them and exit 1
(`Error parsing streaming input line`, the shape `fixtures/cli-stdin-…-a-line-that-is-not-json`
already records).

Re-capture, from the repository root:

    cd factory/server && python3 tests/cli_trust.py

That overwrites `tests/fixtures/cli-trust-<date>-*.json`. `tests/test_agent_trust.py` reads them
and never runs the CLI for the facts they carry.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE_DATE = "2026-07-30"
PREFIX = f"cli-trust-{CAPTURE_DATE}-"

CLAUDE = shutil.which("claude")

# One unparseable line. The CLI prints its settings warnings, fails to parse this, and exits —
# so a cell is a few hundred milliseconds and no tokens.
BAD_STDIN = "NOT JSON\n"

ARGV_TAIL = ["--print", "--input-format", "stream-json",
             "--output-format", "stream-json", "--verbose"]

# What a repository declares. Both keys, because the defect dropped both and the CLI reports them
# on separate lines.
WORKSPACE_SETTINGS = {
    "permissions": {
        "allow": ["Bash(echo har047-allowed:*)", "Read(//tmp/**)"],
        "additionalDirectories": ["/tmp"],
    }
}


def _digest(path):
    p = Path(path)
    if not p.exists():
        return None
    return hashlib.sha256(p.read_bytes()).hexdigest()


def run_cell(name, cwd, home, config_dir, note):
    """One measurement. Returns the record and writes it to `fixtures/`."""
    if CLAUDE is None:
        raise RuntimeError("the real agent CLI is not on PATH")

    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CONFIG_DIR",)}
    env["HOME"] = str(home)
    if config_dir is not None:
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)

    watched = {
        "home_config": str(Path(home) / ".claude.json"),
        "config_dir_config": str(Path(config_dir) / ".claude.json") if config_dir else None,
    }
    before = {k: _digest(v) for k, v in watched.items() if v}

    proc = subprocess.run([CLAUDE] + ARGV_TAIL, input=BAD_STDIN, cwd=str(cwd),
                          env=env, capture_output=True, text=True, timeout=180)

    after = {k: _digest(v) for k, v in watched.items() if v}

    record = {
        "capture": name,
        "date": CAPTURE_DATE,
        "note": note,
        "cli_version": subprocess.run([CLAUDE, "--version"], capture_output=True,
                                      text=True, timeout=60).stdout.strip(),
        "argv": [CLAUDE] + ARGV_TAIL,
        "cwd": str(cwd),
        "env": {"HOME": env["HOME"],
                "CLAUDE_CONFIG_DIR": env.get("CLAUDE_CONFIG_DIR")},
        "stdin": BAD_STDIN,
        "rc": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "stderr_lines": proc.stderr.splitlines(),
        "ignoring_lines": [l for l in proc.stderr.splitlines() if l.startswith("Ignoring ")],
        "watched_files": watched,
        "sha256_before": before,
        "sha256_after": after,
        "files_the_cli_modified": sorted(k for k in before if before[k] != after.get(k)),
    }
    FIXTURES.mkdir(exist_ok=True)
    (FIXTURES / f"{PREFIX}{name}.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    return record


def _workspace(root, name):
    ws = Path(root) / name
    (ws / ".claude").mkdir(parents=True)
    (ws / ".claude" / "settings.json").write_text(json.dumps(WORKSPACE_SETTINGS, indent=2))
    return ws


def _config_home(root, name, trusts=()):
    """A directory holding a `.claude.json`, trusting the given absolute paths."""
    d = Path(root) / name
    d.mkdir(parents=True)
    doc = {"projects": {str(p): {"hasTrustDialogAccepted": True} for p in trusts}}
    (d / ".claude.json").write_text(json.dumps(doc, indent=2))
    return d


def run_turn_cell(name, note, trusted):
    """One REAL turn, to record what the agent GOT rather than what the CLI printed.

    The absence of a warning is a string assertion and the CLI may reword it tomorrow. What
    cannot be reworded is whether the agent could use a tool the repository allowed and no
    built-in rule does — a `Read` outside the working directory, allowed here by
    `permissions.allow` and `permissions.additionalDirectories`, which are exactly the two the
    warning says are dropped.

    Credentials are reached by SYMLINK, never copied: the token stays in the one file the
    developer's own CLI wrote it to, and the capture proves in passing that
    `$CLAUDE_CONFIG_DIR/.credentials.json` is where authentication is read from.
    """
    if CLAUDE is None:
        raise RuntimeError("the real agent CLI is not on PATH")

    root = Path(tempfile.mkdtemp(prefix="har047-turn-"))
    outside = root / "outside"
    outside.mkdir()
    marker = "har047-marker-" + hashlib.sha256(name.encode()).hexdigest()[:12]
    (outside / "marker.txt").write_text(marker + "\n")

    ws = root / "workspace"
    (ws / ".claude").mkdir(parents=True)
    (ws / ".claude" / "settings.json").write_text(json.dumps(
        {"permissions": {"allow": [f"Read({outside}/**)"],
                         "additionalDirectories": [str(outside)]}}, indent=2))

    home = root / "home"
    home.mkdir()
    cfg = root / "config"
    cfg.mkdir()
    (cfg / ".claude.json").write_text(json.dumps(
        {"projects": {str(ws): {"hasTrustDialogAccepted": True}} if trusted else {}}, indent=2))
    credentials = Path(os.path.expanduser("~")) / ".claude" / ".credentials.json"
    (cfg / ".credentials.json").symlink_to(credentials)

    prompt = (f"Use the Read tool on the absolute path {outside}/marker.txt and reply with "
              f"exactly the one line it contains. If you cannot read it, reply CANNOT.")
    frame = json.dumps({"type": "user",
                        "message": {"role": "user",
                                    "content": [{"type": "text", "text": prompt}]}},
                       ensure_ascii=False)

    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CONFIG_DIR"}
    env["HOME"] = str(home)
    env["CLAUDE_CONFIG_DIR"] = str(cfg)
    proc = subprocess.run([CLAUDE] + ARGV_TAIL, input=frame + "\n", cwd=str(ws), env=env,
                          capture_output=True, text=True, timeout=600)

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
        "credentials_are_a_symlink_to": str(credentials),
        "workspace_settings": json.loads((ws / ".claude" / "settings.json").read_text()),
        "config_trusts_the_workspace": trusted,
        "marker": marker,
        "rc": proc.returncode,
        "stderr": proc.stderr,
        "ignoring_lines": [l for l in proc.stderr.splitlines() if l.startswith("Ignoring ")],
        # The three facts every assertion in `test_agent_trust.py` rests on.
        "the_agent_authenticated": any(f.get("type") == "system" for f in frames),
        "the_agent_got_the_bytes": marker in proc.stdout,
        "permission_denials": result.get("permission_denials", []),
        "result_text": result.get("result"),
        "stdout": proc.stdout,
    }
    FIXTURES.mkdir(exist_ok=True)
    (FIXTURES / f"{PREFIX}{name}.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    return record


def capture_worktree_cells(records):
    """WHICH path the CLI keys the trust on, when the working directory is a git worktree.

    The tool checks `projects[<the repository root it was given>]`. If the CLI keyed on
    something else, an operator could add the key the tool asks for, get a green tool, and still
    lose every permission — a check that says yes while the defect is live, which is the class
    this ticket exists to close. So the key is measured rather than assumed, three ways.
    """
    root = Path(tempfile.mkdtemp(prefix="har047-wt-"))
    repo = root / "repo"
    (repo / ".claude").mkdir(parents=True)
    (repo / ".claude" / "settings.json").write_text(json.dumps(WORKSPACE_SETTINGS, indent=2))

    def git(*args, cwd=repo):
        subprocess.run(["git"] + list(args), cwd=str(cwd), check=True,
                       capture_output=True, timeout=60)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "factory@test.local")
    git("config", "user.name", "factory tests")
    git("config", "commit.gpgsign", "false")
    git("add", "-A")
    git("commit", "-qm", "initial")
    git("worktree", "add", "-q", str(repo / "inside" / "wt"), "-b", "inside", "HEAD")
    git("worktree", "add", "-q", str(root / "outside-wt"), "-b", "outside", "HEAD")

    home = _config_home(root, "wt-home")
    cfg = _config_home(root, "wt-config")

    for name, cwd, note in (
            ("worktree-key-is-the-repository-root", repo,
             "the repository's own root: the key is the root."),
            ("worktree-key-from-a-worktree-inside-the-repo", repo / "inside" / "wt",
             "a linked worktree UNDER the root: the key is still the root, not the worktree."),
            ("worktree-key-from-a-worktree-outside-the-repo", root / "outside-wt",
             "a linked worktree OUTSIDE the root: the key is still the root. The CLI resolves "
             "the project through git, so a factory pointed at a linked worktree would check a "
             "key the CLI never reads.")):
        rec = run_cell(name, cwd, home, cfg, note)
        rec["cwd_is_the_repository_root"] = str(cwd) == str(repo)
        rec["repository_root"] = str(repo)
        rec["key_the_cli_named"] = next(
            (m for line in rec["ignoring_lines"]
             for m in re.findall(r'projects\["([^"]+)"\]', line)), None)
        (FIXTURES / f"{PREFIX}{name}.json").write_text(
            json.dumps(rec, indent=2, ensure_ascii=False) + "\n")
        records[name] = rec
    return records


def capture_all():
    """Every cell of the three-way question, plus this repository's live reproduction."""
    records = {}
    root = tempfile.mkdtemp(prefix="har047-trust-")
    ws = _workspace(root, "workspace")

    home_trusting = _config_home(root, "home-trusting", trusts=[ws])
    home_blank = _config_home(root, "home-blank")
    cfg_blank = _config_home(root, "config-blank")
    cfg_trusting = _config_home(root, "config-trusting", trusts=[ws])

    cells = [
        ("home-trusts-and-no-config-dir", ws, home_trusting, None,
         "control: the developer's own situation. Trust in $HOME/.claude.json, no redirection."),
        ("config-dir-set-and-it-has-no-projects", ws, home_trusting, cfg_blank,
         "THE DECIDER. $HOME/.claude.json trusts this workspace and $CLAUDE_CONFIG_DIR/.claude.json "
         "does not. A fallback to $HOME would be quiet here."),
        ("config-dir-carries-the-trust", ws, home_trusting, cfg_trusting,
         "the trust written where the CLI reads it."),
        ("neither-trusts", ws, home_blank, None,
         "control: which file does the CLI name when there is no redirection?"),
        ("config-dir-trusts-but-home-does-not", ws, home_blank, cfg_trusting,
         "a lane: $HOME is empty and carries no trust. $CLAUDE_CONFIG_DIR alone must be enough."),
    ]
    for name, cwd, home, cfg, note in cells:
        records[name] = run_cell(name, cwd, home, cfg, note)

    # The live reproduction: this repository, this developer's home, the manifest's
    # `credentials_dir`. Not hermetic on purpose — it is the defect as it was reported.
    repo_root = Path(__file__).resolve().parents[3]
    real_home = Path(os.path.expanduser("~"))
    records["this-repository-as-the-manifest-runs-it"] = run_cell(
        "this-repository-as-the-manifest-runs-it", repo_root, real_home, real_home / ".claude",
        "the reported defect, reproduced: cwd is this repository, CLAUDE_CONFIG_DIR is the "
        "manifest's agent.credentials_dir.")

    # The two real turns. What the agent GOT, both ways, so no assertion in this ticket rests on
    # the wording of a warning.
    records["turn-trusted-config-the-permission-is-in-force"] = run_turn_cell(
        "turn-trusted-config-the-permission-is-in-force",
        "the config the CLI reads trusts the workspace: the declared Read is allowed and the "
        "agent comes back with the bytes.", trusted=True)
    records["turn-untrusted-config-the-permission-is-dropped"] = run_turn_cell(
        "turn-untrusted-config-the-permission-is-dropped",
        "the same repository, the same declaration, a config that does not trust it: the Read is "
        "denied and the agent never sees the bytes. This is the defect, priced in what it costs.",
        trusted=False)

    return records


if __name__ == "__main__":
    for name, rec in capture_all().items():
        marks = rec["ignoring_lines"]
        print(f"{name}: rc={rec['rc']} ignoring={len(marks)} "
              f"modified={rec['files_the_cli_modified'] or 'nothing'}")
        for line in marks:
            print("   ", line[:160])
