"""Record the installed app-server against an explicitly authorized synthetic repository."""

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import tempfile
import time


class Connection:
    def __init__(self, binary, root, home, errors):
        self.proc = subprocess.Popen([binary, "app-server", "--listen", "stdio://"],
                                     cwd=root, env=dict(os.environ, CODEX_HOME=str(home)),
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors)
        self.records = []
        self.buffer = b""
        self.phase = "initialize"

    def send(self, frame):
        raw = json.dumps(frame) + "\n"
        self.records.append({"direction": "client", "phase": self.phase, "raw": raw})
        self.proc.stdin.write(raw.encode())
        self.proc.stdin.flush()

    def until(self, accepts, timeout=150):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if b"\n" not in self.buffer:
                if not select.select([self.proc.stdout], [], [], min(1, deadline - time.monotonic()))[0]:
                    continue
                chunk = os.read(self.proc.stdout.fileno(), 65536)
                if not chunk:
                    raise RuntimeError("app-server exited before the expected frame")
                self.buffer += chunk
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                raw = line.decode() + "\n"
                self.records.append({"direction": "server", "phase": self.phase, "raw": raw})
                frame = json.loads(raw)
                if accepts(frame):
                    return frame
        raise TimeoutError(f"app-server did not finish {self.phase}")

    def rpc(self, identity, method, params):
        self.send({"id": identity, "method": method, "params": params})
        return self.until(lambda frame: frame.get("id") == identity and "method" not in frame)

    def close(self):
        self.proc.stdin.close()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    auth = args.credentials / "auth.json"
    auth_before = hashlib.sha256(auth.read_bytes()).digest()
    capture = {"captured": datetime.datetime.now(datetime.timezone.utc).isoformat(),
               "cli_version": subprocess.check_output([args.binary, "--version"], text=True).strip(),
               "model": args.model, "purpose": "synthetic app-server protocol capture; no private repository content"}
    with tempfile.TemporaryDirectory(prefix="srv-feat-558-protocol-") as scratch:
        root = Path(scratch) / "repo"
        root.mkdir()
        home = Path(scratch) / "agent-config"
        home.mkdir()
        shutil.copyfile(auth, home / "auth.json")
        (root / "modify.txt").write_text("before\nkeep\n")
        (root / "delete.txt").write_text("remove me\n")
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        with tempfile.TemporaryFile() as errors:
            connection = Connection(args.binary, root, home, errors)
            try:
                initialized = connection.rpc(1, "initialize", {"clientInfo": {
                    "name": "factory_protocol_capture", "version": "1"},
                    "capabilities": {"experimentalApi": True}})
                assert "result" in initialized, initialized
                connection.send({"method": "initialized", "params": {}})
                connection.phase = "initialization-error"
                assert "error" in connection.rpc(2, "initialize", {"clientInfo": {
                    "name": "factory_protocol_capture", "version": "1"}})
                connection.phase = "thread-start"
                started = connection.rpc(3, "thread/start", {"model": args.model, "cwd": str(root),
                    "approvalPolicy": "never", "sandbox": "workspace-write"})
                thread = started["result"]["thread"]["id"]
                connection.phase = "edit-turn"
                connection.rpc(4, "turn/start", {"threadId": thread, "input": [{"type": "text", "text":
                    "This is an authorized synthetic protocol fixture. Use apply_patch (not shell writes) "
                    "in ONE patch to create created.txt containing exactly new\\n, change modify.txt "
                    "from before\\nkeep\\n to after\\nkeep\\n, and delete delete.txt. "
                    "Do not read or modify any other files. Then briefly confirm completion."}]})
                completed = connection.until(lambda frame: frame.get("method") == "turn/completed")
                assert completed["params"]["turn"]["status"] == "completed", completed
                assert (root / "created.txt").read_text() == "new\n"
                assert (root / "modify.txt").read_text() == "after\nkeep\n"
                assert not (root / "delete.txt").exists()
                connection.phase = "second-turn"
                connection.rpc(5, "turn/start", {"threadId": thread, "input": [{"type": "text", "text":
                    "Run the read-only shell command pwd once, then finish without editing any file."}]})
                completed = connection.until(lambda frame: frame.get("method") == "turn/completed")
                assert completed["params"]["turn"]["status"] == "completed", completed
                connection.phase = "thread-resume"
                resumed = connection.rpc(6, "thread/resume", {"threadId": thread, "model": args.model,
                    "cwd": str(root), "approvalPolicy": "never", "sandbox": "workspace-write"})
                assert resumed["result"]["thread"]["id"] == thread
                connection.phase = "interrupt-turn"
                turn = connection.rpc(7, "turn/start", {"threadId": thread, "input": [{"type": "text", "text":
                    "Run the shell command sleep 30 now. Do not edit any files."}]})["result"]["turn"]["id"]
                connection.until(lambda frame: frame.get("method") == "item/started" and
                    frame.get("params", {}).get("item", {}).get("type") == "commandExecution")
                connection.rpc(8, "turn/interrupt", {"threadId": thread, "turnId": turn})
                interrupted = connection.until(lambda frame: frame.get("method") == "turn/completed")
                assert interrupted["params"]["turn"]["status"] == "interrupted", interrupted
                connection.phase = "approval-thread"
                approved_thread = connection.rpc(9, "thread/start", {"model": args.model, "cwd": str(root),
                    "approvalPolicy": "untrusted", "sandbox": "read-only"})["result"]["thread"]["id"]
                for identity, decision in ((10, "decline"), (11, "accept")):
                    connection.phase = "approval-" + decision
                    connection.rpc(identity, "turn/start", {"threadId": approved_thread,
                        "input": [{"type": "text", "text":
                            "Run the shell command printf approved > approval.txt in the current "
                            "synthetic repository. Request permission if required. If permission "
                            "is denied, do not retry or use another tool; finish the turn."}]})
                    approval = connection.until(lambda frame:
                        frame.get("method") == "item/commandExecution/requestApproval")
                    connection.send({"id": approval["id"], "result": {"decision": decision}})
                    connection.until(lambda frame: frame.get("method") == "turn/completed")
                    assert (root / "approval.txt").exists() == (decision == "accept")
                capture["fixture_verified"] = True
            finally:
                connection.close()
                errors.seek(0)
                capture.update(records=connection.records, stderr=errors.read().decode(),
                               process_exit=connection.proc.returncode,
                               credentials_unchanged=hashlib.sha256(auth.read_bytes()).digest() == auth_before)
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(capture, indent=2) + "\n")
    assert capture["process_exit"] == 0, capture["process_exit"]
    print(json.dumps({key: capture.get(key) for key in
                      ("cli_version", "fixture_verified", "process_exit", "credentials_unchanged")}))


if __name__ == "__main__":
    main()
