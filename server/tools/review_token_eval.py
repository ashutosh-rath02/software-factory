#!/usr/bin/env python3
"""Capture two REVIEW prompts on one commit, then optionally measure Codex usage."""

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path(".claude/scripts/review.sh")
MODEL = "gpt-6-sol"
FINDING = re.compile(r"^(blocker|minor) [^ ]+:[0-9]+ .+$")
READ = re.compile(r"\b(cat|sed|rg|grep|head|tail|git show|git diff)\b")


def git(*args, cwd=ROOT):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True).stdout


def digest(data):
    return hashlib.sha256(data).hexdigest()


def save_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


@contextmanager
def instruction_snapshot(clone):
    paths = [clone / name for name in ("docs/technical/workflow/REVIEW.md", "docs/REVIEW.md")]
    existing = [path for path in paths if path.exists()]
    if not existing:
        raise ValueError("selected HEAD has no REVIEW instructions")
    if any(not path.is_file() or not path.resolve().is_relative_to(clone.resolve())
           for path in existing):
        raise ValueError("REVIEW instructions must be files inside the selected snapshot")
    docs = existing[0].read_bytes()
    if any(path.read_bytes() != docs for path in existing[1:]):
        raise ValueError("conflicting REVIEW instructions in selected HEAD")
    aliases, directories = [], []
    try:
        for path in paths:
            if path in existing:
                continue
            if not path.resolve().is_relative_to(clone.resolve()):
                raise ValueError("REVIEW instruction alias escapes the selected snapshot")
            missing = []
            parent = path.parent
            while not parent.exists():
                missing.append(parent)
                parent = parent.parent
            for directory in reversed(missing):
                directory.mkdir()
                directories.append(directory)
            with path.open("xb") as destination:
                aliases.append(path)
                destination.write(docs)
        yield docs
    finally:
        for path in reversed(aliases):
            path.unlink()
        for directory in reversed(directories):
            directory.rmdir()


def capture(script, label, base, clone, output):
    bin_dir = output / "capture-bin"
    bin_dir.mkdir(exist_ok=True)
    shim_code = """#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
driver = pathlib.Path(sys.argv[0]).name
assert driver in ('codex', 'claude')
if driver == 'codex':
    assert args[0] == 'exec'
else:
    assert '-p' in args
model = args[args.index('--model') + 1] if '--model' in args else None
pathlib.Path(os.environ['REVIEW_EVAL_PROMPT']).write_bytes(sys.stdin.buffer.read())
pathlib.Path(os.environ['REVIEW_EVAL_INVOCATION']).write_text(json.dumps({'driver': driver, 'model': model}))
if driver == 'codex':
    pathlib.Path(args[args.index('--output-last-message') + 1]).write_bytes(b'')
"""
    for name in ("codex", "claude"):
        shim = bin_dir / name
        shim.write_text(shim_code)
        shim.chmod(0o755)
    prompt = output / f"{label}.prompt"
    invocation = output / f"{label}.capture.json"
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}",
               FACTORY_REVIEW_DRIVER="codex", FACTORY_REVIEW_MODEL=MODEL,
               REVIEW_EVAL_PROMPT=str(prompt), REVIEW_EVAL_INVOCATION=str(invocation))
    ran = subprocess.run(["sh", str(script), base], cwd=clone, env=env,
                         capture_output=True, timeout=60)
    if (ran.returncode or not prompt.is_file() or not invocation.is_file()
            or b"review: outcome=ran" not in ran.stderr):
        raise RuntimeError(f"{label} prompt capture failed: rc={ran.returncode}, "
                           f"stderr={ran.stderr.decode(errors='replace')}")
    return prompt.read_bytes(), json.loads(invocation.read_text())


def parse_events(data):
    usage = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}
    turns = 0
    tool_calls = 0
    readbacks = 0
    for line in data.splitlines():
        event = json.loads(line)
        kind = event.get("type")
        if kind == "turn.failed":
            raise ValueError("turn.failed event")
        if kind == "turn.completed":
            found = event.get("usage")
            if not isinstance(found, dict):
                raise ValueError("completed turn has no usage")
            for key in usage:
                value = found.get(key)
                if type(value) is not int or value < 0:
                    raise ValueError(f"missing or invalid {key}")
                usage[key] += value
            if found["cached_input_tokens"] > found["input_tokens"]:
                raise ValueError("cached input exceeds input")
            turns += 1
        if kind == "item.completed":
            item = event.get("item") or {}
            if item.get("type") in {"command_execution", "mcp_tool_call", "web_search"}:
                tool_calls += 1
                if item.get("type") == "command_execution" and READ.search(item.get("command", "")):
                    readbacks += 1
    if not turns:
        raise ValueError("no completed turn with usage")
    usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
    return dict(usage=usage, completed_turns=turns, tool_calls=tool_calls,
                readbacks=readbacks)


def run_arm(label, prompt, clone, output, codex, timeout):
    stdout = output / f"{label}.jsonl"
    stderr = output / f"{label}.stderr"
    final = output / f"{label}.final"
    command = [codex, "exec", "--model", MODEL, "--sandbox", "read-only",
               "--ephemeral", "--json", "--color", "never", "--ignore-user-config",
               "--disable", "multi_agent", "-c", "model_reasoning_effort=\"medium\"",
               "--output-last-message", str(final), "-"]
    started = time.monotonic()
    result = dict(command=command[1:], model=MODEL, status="failed")
    try:
        ran = subprocess.run(command, input=prompt, cwd=clone, capture_output=True,
                             timeout=timeout)
        out, err = ran.stdout, ran.stderr
        result["returncode"] = ran.returncode
    except subprocess.TimeoutExpired as exc:
        out, err = exc.stdout or b"", exc.stderr or b""
        result["error"] = f"timeout after {timeout} seconds"
    result["duration_seconds"] = round(time.monotonic() - started, 3)
    stdout.write_bytes(out)
    stderr.write_bytes(err)
    if "returncode" not in result or result["returncode"]:
        result.setdefault("error", "Codex exited nonzero")
        return result
    try:
        if not final.is_file():
            raise ValueError("missing final answer file")
        answer = final.read_text()
        if answer.strip() and any(not FINDING.fullmatch(line) for line in answer.splitlines()):
            raise ValueError("invalid final answer format")
        result.update(parse_events(out))
        if git("status", "--porcelain", cwd=clone):
            raise ValueError("reviewed snapshot changed")
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        result["error"] = str(exc)
        return result
    result["status"] = "complete"
    return result


def evaluate(args):
    source = Path(args.repo).resolve()
    output = Path(args.output).resolve()
    if output == source or source in output.parents:
        raise ValueError("output must be outside the source repository")
    source_head = git("rev-parse", "HEAD", cwd=source).decode().strip()
    base = git("rev-parse", "--verify", f"{args.base}^{{commit}}", cwd=source).decode().strip()
    head = git("rev-parse", "--verify", f"{args.head}^{{commit}}", cwd=source).decode().strip()
    baseline_ref = git("merge-base", base, head, cwd=source).decode().strip()
    baseline = git("show", f"{baseline_ref}:{SCRIPT}", cwd=source)
    candidate = (source / SCRIPT).read_bytes()
    output.mkdir(parents=True, exist_ok=False)
    (output / "baseline.review.sh").write_bytes(baseline)
    (output / "candidate.review.sh").write_bytes(candidate)
    with tempfile.TemporaryDirectory(prefix="review-token-eval-") as temp:
        clone = Path(temp) / "snapshot"
        subprocess.run(["git", "clone", "--no-hardlinks", "--no-checkout", "-q",
                        str(source), str(clone)], check=True)
        git("checkout", "--detach", "-q", head, cwd=clone)
        names = git("diff", "--name-only", "-z", f"{base}...{head}", cwd=clone)
        changed = [os.fsdecode(name) for name in names.split(b"\0") if name]
        included = [name for name in changed if not name.startswith("docs/reports/")]
        omitted = [name for name in changed if name.startswith("docs/reports/")]
        prompts = {}
        invocations = {}
        with instruction_snapshot(clone) as docs:
            for label in ("baseline", "candidate"):
                prompts[label], invocations[label] = capture(
                    output / f"{label}.review.sh", label, base, clone, output)
        if git("status", "--porcelain", cwd=clone):
            raise ValueError("prompt capture changed reviewed snapshot")
        comparison_status = ("identical_scripts" if baseline == candidate else
                             "identical_prompts" if prompts["baseline"] == prompts["candidate"]
                             else "incompatible_driver" if any(
                                 invocation != {"driver": "codex", "model": MODEL}
                                 for invocation in invocations.values()) else "comparable")
        manifest = {
            "source_repo": str(source), "source_head": source_head,
            "base": base, "head": head, "baseline_script_ref": baseline_ref,
            "comparison_status": comparison_status, "model": MODEL,
            "review_instructions_sha256": digest(docs),
            "changed_files": dict(all=changed, included=included, omitted_reports=omitted),
            "arms": {label: {"script_sha256": digest(script),
                             "capture_invocation": invocations[label],
                             "prompt_sha256": digest(prompts[label]),
                             "prompt_bytes": len(prompts[label])}
                     for label, script in (("baseline", baseline), ("candidate", candidate))},
        }
        save_json(output / "manifest.json", manifest)
        if comparison_status != "comparable":
            reason = {"identical_scripts": "identical REVIEW scripts",
                      "identical_prompts": "identical REVIEW prompts",
                      "incompatible_driver": "REVIEW capture did not use codex gpt-6-sol for both arms"}
            raise ValueError(f"{reason[comparison_status]}; no paired savings comparison is valid")
        if not args.run:
            return 0
        codex = shutil.which("codex")
        if not codex:
            raise ValueError("codex is unavailable")
        order = ["candidate", "baseline"] if args.reverse_order else ["baseline", "candidate"]
        report = dict(manifest="manifest.json", order=order, arms={})
        for label in order:
            report["arms"][label] = run_arm(label, prompts[label], clone, output, codex, args.timeout)
            save_json(output / "report.json", report)
        if all(arm["status"] == "complete" for arm in report["arms"].values()):
            old = report["arms"]["baseline"]["usage"]["total_tokens"]
            new = report["arms"]["candidate"]["usage"]["total_tokens"]
            report["paired_delta_tokens"] = new - old
            report["paired_savings_tokens"] = old - new
            report["paired_savings_percent"] = round((old - new) / old * 100, 2) if old else None
        save_json(output / "report.json", report)
        return 0 if "paired_delta_tokens" in report else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--repo", default=str(ROOT))
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--reverse-order", action="store_true")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    try:
        return evaluate(args)
    except (OSError, subprocess.CalledProcessError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"review token eval: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
