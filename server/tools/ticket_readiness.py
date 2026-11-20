#!/usr/bin/env python3
"""Run repository-declared readiness probes and retain evidence-backed cause state.

Red and negative receipts may describe deliberate reversions of the candidate artifact.
Their shared artifact hash identifies the candidate being tested, not the reverted bytes.
QA receipts are workflow attestations; this tool does not perform independent QA.
"""

import argparse
import fcntl
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path


PHASES = ("red", "green", "negative", "qa")
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
HASH = re.compile(r"^[0-9a-f]{64}$")


class Invalid(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise Invalid(message)


def file_hash(path):
    require(path.is_file() and path.stat().st_size > 0, f"missing or empty file: {path}")
    hashed = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hashed.update(chunk)
    return hashed.hexdigest()


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Invalid(f"cannot read JSON {path}: {exc}") from exc


def strings(value, name):
    require(isinstance(value, list) and bool(value), f"{name} must be a nonempty list")
    require(all(isinstance(item, str) and ID.fullmatch(item) for item in value),
            f"{name} contains an invalid identifier")
    require(len(set(value)) == len(value), f"{name} contains a duplicate")


def refs(value, name):
    require(isinstance(value, list) and bool(value), f"{name} must be a nonempty list")
    require(all(isinstance(item, str) and item.strip() and "\x00" not in item for item in value),
            f"{name} contains an invalid reference")
    require(len(set(value)) == len(value), f"{name} contains a duplicate")


def validate_resolution(value):
    require(isinstance(value, dict) and set(value) == {"artifact_sha256", "artifact", "receipts"},
            "malformed resolution")
    require(isinstance(value["artifact_sha256"], str) and HASH.fullmatch(value["artifact_sha256"]),
            "invalid resolved artifact hash")
    require(isinstance(value["artifact"], str) and bool(value["artifact"]),
            "invalid resolved artifact path")
    receipts = value["receipts"]
    require(isinstance(receipts, dict) and set(receipts) == set(PHASES),
            "resolution must retain all four receipts")
    for phase, receipt in receipts.items():
        require(isinstance(receipt, dict) and set(receipt) == {
            "path", "receipt_sha256", "evidence", "evidence_sha256", "outcome"},
            f"malformed {phase} receipt record")
        for key in ("path", "evidence"):
            require(isinstance(receipt[key], str) and bool(receipt[key]),
                    f"invalid {phase} {key}")
        for key in ("receipt_sha256", "evidence_sha256"):
            require(isinstance(receipt[key], str) and HASH.fullmatch(receipt[key]),
                    f"invalid {phase} {key}")
        require(receipt["outcome"] == ("pass" if phase in ("green", "qa") else "fail"),
                f"invalid {phase} outcome")


def validate_cause(cause, *, incoming=False):
    required = {"id", "scope", "fix_tickets", "probes", "evidence", "incidents", "status"}
    allowed = required | {"resolution", "previous_resolutions"}
    require(isinstance(cause, dict) and required <= set(cause) and set(cause) <= allowed,
            "cause has missing or unknown fields")
    require(isinstance(cause["id"], str) and ID.fullmatch(cause["id"]), "invalid cause id")
    scope = cause["scope"]
    require(isinstance(scope, dict) and set(scope) == {"workspaces", "profiles"},
            "scope must name workspaces and profiles")
    strings(scope["workspaces"], "scope.workspaces")
    strings(scope["profiles"], "scope.profiles")
    strings(cause["fix_tickets"], "fix_tickets")
    refs(cause["evidence"], "evidence")
    strings(cause["incidents"], "incidents")
    probes = cause["probes"]
    require(isinstance(probes, list) and bool(probes), "probes must be nonempty")
    for probe in probes:
        require(isinstance(probe, dict) and set(probe) == {"argv", "timeout_seconds"},
                "probe must have argv and timeout_seconds only")
        argv = probe["argv"]
        require(isinstance(argv, list) and bool(argv)
                and all(isinstance(part, str) and part and "\x00" not in part for part in argv),
                "probe argv must be nonempty strings")
        seconds = probe["timeout_seconds"]
        require(type(seconds) is int and 1 <= seconds <= 300,
                "probe timeout_seconds must be 1..300")
    require(cause["status"] in ("open", "resolved"), "invalid cause status")
    if incoming:
        require(cause["status"] == "open" and set(cause) == required,
                "incoming cause must be open without resolution")
    if cause["status"] == "resolved":
        require("resolution" in cause, "resolved cause needs resolution")
    else:
        require("resolution" not in cause, "open cause cannot have active resolution")
    if "resolution" in cause:
        validate_resolution(cause["resolution"])
    if "previous_resolutions" in cause:
        require(isinstance(cause["previous_resolutions"], list)
                and bool(cause["previous_resolutions"]), "invalid previous resolutions")
        for item in cause["previous_resolutions"]:
            validate_resolution(item)


def registry_at(path):
    doc = read_json(path)
    require(isinstance(doc, dict) and set(doc) == {"version", "causes"}
            and type(doc["version"]) is int and doc["version"] == 1
            and isinstance(doc["causes"], list), "registry must have version 1 and causes")
    ids = set()
    for cause in doc["causes"]:
        validate_cause(cause)
        require(cause["id"] not in ids, f"duplicate cause id: {cause['id']}")
        ids.add(cause["id"])
    return doc


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as destination:
            json.dump(value, destination, indent=2, sort_keys=True)
            destination.write("\n")
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def mutation_lock(path):
    lock = path.with_name(path.name + ".lock")
    with lock.open("a+b") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def definition(cause):
    return {key: cause[key] for key in ("id", "scope", "fix_tickets", "probes")}


def match_cause(doc, incoming):
    return next((cause for cause in doc["causes"] if cause["id"] == incoming["id"]), None)


def record(args, *, preview=False):
    incoming = read_json(args.cause_file)
    validate_cause(incoming, incoming=True)
    if preview:
        doc = registry_at(args.registry)
        existing = match_cause(doc, incoming)
        if existing and definition(existing) != definition(incoming):
            raise Invalid(f"cause id {incoming['id']} has a conflicting definition")
        return dict(status="match" if existing else "new", cause_id=incoming["id"],
                    incidents=sorted(set(incoming["incidents"]) |
                                     (set(existing["incidents"]) if existing else set())),
                    evidence=sorted(set(incoming["evidence"]) |
                                    (set(existing["evidence"]) if existing else set())))
    with mutation_lock(args.registry):
        doc = registry_at(args.registry)
        existing = match_cause(doc, incoming)
        if existing:
            require(definition(existing) == definition(incoming),
                    f"cause id {incoming['id']} has a conflicting definition")
            new = set(incoming["incidents"]) - set(existing["incidents"])
            existing["incidents"] = sorted(set(existing["incidents"]) | set(incoming["incidents"]))
            existing["evidence"] = sorted(set(existing["evidence"]) | set(incoming["evidence"]))
            if new and existing["status"] == "resolved":
                existing.setdefault("previous_resolutions", []).append(existing.pop("resolution"))
                existing["status"] = "open"
        else:
            incoming["incidents"] = sorted(incoming["incidents"])
            incoming["evidence"] = sorted(incoming["evidence"])
            doc["causes"].append(incoming)
        doc["causes"].sort(key=lambda cause: cause["id"])
        atomic_json(args.registry, doc)
    return dict(status="recorded", cause_id=incoming["id"],
                incidents=(existing or incoming)["incidents"],
                evidence=(existing or incoming)["evidence"])


def check(args):
    doc = registry_at(args.registry)
    applicable = []
    exempt = []
    for cause in doc["causes"]:
        scope = cause["scope"]
        if args.workspace not in scope["workspaces"] or args.profile not in scope["profiles"]:
            continue
        if cause["status"] == "open" and args.ticket in cause["fix_tickets"]:
            exempt.append(cause["id"])
        else:
            applicable.append(cause)
    results = []
    if applicable:
        args.captures.mkdir(parents=True, exist_ok=True)
    for cause in sorted(applicable, key=lambda item: item["id"]):
        probes = []
        for index, probe in enumerate(cause["probes"]):
            prefix = f"{cause['id']}-{index}-{uuid.uuid4().hex}"
            stdout = args.captures / f"{prefix}.stdout"
            stderr = args.captures / f"{prefix}.stderr"
            result = dict(argv=probe["argv"], exit_code=None, status="error",
                          stdout_path=str(stdout), stderr_path=str(stderr))
            with stdout.open("wb") as out, stderr.open("wb") as err:
                try:
                    process = subprocess.Popen(probe["argv"], stdout=out, stderr=err,
                                               start_new_session=True)
                    try:
                        code = process.wait(timeout=probe["timeout_seconds"])
                        result["exit_code"] = code
                        result["status"] = "pass" if code == 0 else "fail"
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait()
                        result["status"] = "timeout"
                except OSError as exc:
                    err.write(str(exc).encode())
            probes.append(result)
        results.append(dict(id=cause["id"], status=cause["status"],
                            fix_tickets=cause["fix_tickets"], probe_results=probes))
    unknown = any(probe["status"] in ("timeout", "error")
                  for cause in results for probe in cause["probe_results"])
    blocked = any(cause["status"] == "open" or
                  any(probe["status"] != "pass" for probe in cause["probe_results"])
                  for cause in results)
    status = "error" if unknown else "blocked" if blocked else "ready" if results else "no_applicable_checks"
    report = dict(version=1, status=status, checked=len(results), exempt=sorted(exempt),
                  causes=results)
    if unknown:
        report["message"] = "one or more probes timed out or could not run"
    return report


def receipt_at(path, phase, cause_id, artifact_sha256):
    value = read_json(path)
    required = {"version", "phase", "cause_id", "artifact_sha256", "outcome", "evidence"}
    extras = {"session_id", "independent"} if phase == "qa" else {"exit_code"}
    require(isinstance(value, dict) and set(value) == required | extras,
            f"{phase} receipt has missing or unknown fields")
    require(type(value["version"]) is int and value["version"] == 1
            and value["phase"] == phase and value["cause_id"] == cause_id,
            f"{phase} receipt identifies wrong phase or cause")
    require(value["artifact_sha256"] == artifact_sha256,
            f"{phase} receipt identifies wrong artifact")
    require(value["outcome"] == ("pass" if phase in ("green", "qa") else "fail"),
            f"{phase} receipt has wrong outcome")
    if phase == "qa":
        require(value["independent"] is True and isinstance(value["session_id"], str)
                and bool(value["session_id"].strip()), "QA receipt needs independent session attestation")
    else:
        code = value["exit_code"]
        require(type(code) is int and (code == 0 if phase == "green" else code != 0),
                f"{phase} receipt has wrong exit_code")
    evidence = value["evidence"]
    require(isinstance(evidence, str) and bool(evidence.strip()) and "\x00" not in evidence,
            f"{phase} receipt has invalid evidence path")
    evidence_path = Path(evidence)
    if not evidence_path.is_absolute():
        evidence_path = path.parent / evidence_path
    return dict(path=str(path), receipt_sha256=file_hash(path),
                evidence=str(evidence_path), evidence_sha256=file_hash(evidence_path),
                outcome=value["outcome"])


def resolve(args):
    artifact_sha256 = file_hash(args.artifact)
    with mutation_lock(args.registry):
        doc = registry_at(args.registry)
        cause = next((item for item in doc["causes"] if item["id"] == args.id), None)
        require(cause is not None, f"unknown cause id: {args.id}")
        receipts = {phase: receipt_at(getattr(args, phase), phase, args.id, artifact_sha256)
                    for phase in PHASES}
        resolution = dict(artifact=str(args.artifact), artifact_sha256=artifact_sha256,
                          receipts=receipts)
        if cause["status"] == "resolved":
            require(cause["resolution"] == resolution, "cause already resolved with other evidence")
        else:
            cause["status"] = "resolved"
            cause["resolution"] = resolution
            atomic_json(args.registry, doc)
    return dict(status="resolved", cause_id=args.id,
                artifact_sha256=artifact_sha256,
                receipts={phase: receipts[phase]["path"] for phase in PHASES})


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    for name in ("record", "dedup", "check", "resolve"):
        sub = modes.add_parser(name)
        sub.add_argument("--registry", type=Path, required=True)
        sub.add_argument("--output", type=Path, required=True)
        if name in ("record", "dedup"):
            sub.add_argument("--cause-file", type=Path, required=True)
        if name == "check":
            sub.add_argument("--workspace", required=True)
            sub.add_argument("--profile", required=True)
            sub.add_argument("--ticket", required=True)
            sub.add_argument("--captures", type=Path, required=True)
        if name == "resolve":
            sub.add_argument("--id", required=True)
            sub.add_argument("--artifact", type=Path, required=True)
            for phase in PHASES:
                sub.add_argument(f"--{phase}", type=Path, required=True)
    return parser.parse_args()


def stdout_summary(output, mode):
    summary = {key: output[key] for key in ("status", "checked", "cause_id")
               if key in output}
    if "message" in output:
        summary["message"] = output["message"][:256]
    if mode == "check":
        blockers = [cause for cause in output.get("causes", [])
                    if cause["status"] == "open" or any(
                        probe["status"] != "pass" for probe in cause["probe_results"])]
        cause_ids = sorted(cause["id"] for cause in blockers)
        fixes = sorted({ticket for cause in blockers for ticket in cause["fix_tickets"]})
        exempt = sorted(output.get("exempt", []))
        for field, values in (("blocking_causes", cause_ids), ("fix_tickets", fixes),
                              ("exempt", exempt)):
            summary[field] = values[:8]
            if len(values) > 8:
                summary[f"omitted_{field}"] = len(values) - 8
    return summary


def main():
    args = arguments()
    inputs = [args.registry]
    if args.mode in ("record", "dedup"):
        inputs.append(args.cause_file)
    if args.mode == "resolve":
        inputs.extend([args.artifact, *(getattr(args, phase) for phase in PHASES)])
    if args.output.resolve() in {path.resolve() for path in inputs}:
        print("ticket readiness: output must differ from every input", file=sys.stderr)
        return 2
    try:
        if args.mode == "record":
            output = record(args)
        elif args.mode == "dedup":
            output = record(args, preview=True)
        elif args.mode == "check":
            output = check(args)
        else:
            output = resolve(args)
        code = 2 if output["status"] == "error" else 1 if output["status"] == "blocked" else 0
    except (Invalid, OSError) as exc:
        output = dict(version=1, status="error", message=str(exc))
        code = 2
    atomic_json(args.output, output)
    print(json.dumps(stdout_summary(output, args.mode), sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
