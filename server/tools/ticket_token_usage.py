#!/usr/bin/env python3
"""Collect Codex rollout token totals for Factory runs without contacting a provider."""

import argparse
import hashlib
import json
import sys
from pathlib import Path


MODEL = "gpt-6-sol"
TOKEN_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens")
# Providers bill a cached input token at roughly a tenth of a fresh one and an output token in
# full; raw input totals overstate cost about ninefold at a 99% cache hit rate (2026-10-11).
CACHED_WEIGHT = 0.1


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def usage_of(info):
    if not isinstance(info, dict) or not isinstance(info.get("total_token_usage"), dict):
        return None
    raw = info["total_token_usage"]
    if any(type(raw.get(key)) is not int or raw[key] < 0 for key in TOKEN_FIELDS):
        return None
    if raw["cached_input_tokens"] > raw["input_tokens"]:
        return None
    usage = {key: raw[key] for key in TOKEN_FIELDS}
    usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
    return usage


def read_rollout(path, wanted):
    session_id = None
    latest = None
    warnings = []
    with path.open(encoding="utf-8") as source:
        for number, line in enumerate(source, 1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                warnings.append(f"invalid JSON at line {number}")
                continue
            payload = event.get("payload") or {}
            if event.get("type") == "session_meta":
                session_id = payload.get("id") or payload.get("session_id")
                if session_id not in wanted:
                    return None
            elif session_id in wanted and event.get("type") == "event_msg" and payload.get("type") == "token_count":
                current = usage_of(payload.get("info"))
                if current is not None:
                    latest = current
    if session_id not in wanted:
        return None
    return dict(session_id=session_id, usage=latest, path=str(path),
                sha256=sha256(path), warnings=warnings)


def collect(runs_dir, session_roots):
    runs = []
    problems = []
    for path in sorted(runs_dir.glob("*/meta.json")):
        try:
            meta = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(dict(path=str(path), error=str(exc)))
            continue
        if meta.get("driver") != "codex":
            continue
        runs.append(dict(run_id=meta.get("run_id"), session_id=meta.get("session_id"),
                         ticket_id=meta.get("ticket_id"), lane=meta.get("lane"),
                         role=meta.get("role"), agent=meta.get("agent"),
                         driver=meta.get("driver"), model=meta.get("model"),
                         started_iso=meta.get("started_iso"), ended_iso=meta.get("ended_iso"),
                         outcome=meta.get("outcome"), meta_path=str(path),
                         meta_sha256=sha256(path)))
    by_session = {}
    for run in runs:
        if run["session_id"]:
            by_session.setdefault(run["session_id"], []).append(run)
    rollouts = {session_id: [] for session_id in by_session}
    for root in session_roots:
        for path in sorted(root.rglob("rollout-*.jsonl")):
            try:
                found = read_rollout(path, by_session)
            except (OSError, UnicodeDecodeError) as exc:
                problems.append(dict(path=str(path), error=str(exc)))
                continue
            if found:
                rollouts[found["session_id"]].append(found)
    sessions = []
    for session_id, group in sorted(by_session.items()):
        files = rollouts[session_id]
        known = [item for item in files if item["usage"] is not None]
        chosen = max(known, key=lambda item: item["usage"]["total_tokens"]) if known else None
        models = sorted({run["model"] for run in group if run["model"]})
        match = all(run["model"] == MODEL for run in group)
        sessions.append(dict(session_id=session_id,
                             run_ids=sorted(run["run_id"] for run in group),
                             ticket_ids=sorted({run["ticket_id"] for run in group if run["ticket_id"]}),
                             shared_session=len(group) > 1, models=models,
                             model_match=match,
                             status="model_mismatch" if not match else
                                    "known" if chosen else "unknown_usage",
                             usage=chosen["usage"] if chosen else None,
                             chosen_rollout=chosen["path"] if chosen else None,
                             rollouts=files))
    for run in runs:
        if not run["session_id"]:
            problems.append(dict(path=run["meta_path"], error="missing session_id"))
    eligible = [item for item in sessions if item["model_match"]]
    known = [item for item in eligible if item["usage"] is not None]
    totals = {key: sum(item["usage"][key] for item in known)
              for key in (*TOKEN_FIELDS, "total_tokens")}
    complete = bool(runs) and len(eligible) == len(known) and not problems and \
               len(eligible) == len(sessions) and \
               len(runs) == sum(len(group) for group in by_session.values())
    return dict(expected_model=MODEL, runs_dir=str(runs_dir),
                session_roots=[str(root) for root in session_roots],
                runs=runs, sessions=sessions, problems=problems,
                summary=dict(codex_runs=len(runs), distinct_sessions=len(sessions),
                             status="incomplete" if problems else "no_codex_runs" if not runs else
                                    "complete" if complete else "incomplete",
                             shared_sessions=sum(item["shared_session"] for item in sessions),
                             matching_sessions=len(eligible), known_sessions=len(known),
                             unknown_sessions=len(eligible) - len(known),
                             model_mismatch_sessions=len(sessions) - len(eligible),
                             known_usage_only=totals if runs else None,
                             effective_tokens=round(
                                 totals["input_tokens"] - totals["cached_input_tokens"]
                                 + CACHED_WEIGHT * totals["cached_input_tokens"]
                                 + totals["output_tokens"]) if runs else None,
                             complete=complete))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-dir", type=Path, required=True)
    parser.add_argument("--session-root", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    runs_dir = args.runs_dir.resolve()
    roots = [root.resolve() for root in args.session_root]
    output = args.output.resolve()
    try:
        if not runs_dir.is_dir() or any(not root.is_dir() for root in roots):
            raise ValueError("runs-dir and every session-root must be directories")
        if any(output == root or root in output.parents for root in [runs_dir, *roots]):
            raise ValueError("output must be outside the input roots")
        if output.exists():
            raise ValueError("output already exists")
        report = collect(runs_dir, roots)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(json.dumps(report["summary"], sort_keys=True))
        return 0
    except (OSError, ValueError) as exc:
        print(f"ticket token usage: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
