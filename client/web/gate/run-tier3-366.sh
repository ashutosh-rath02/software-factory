#!/usr/bin/env bash
# WEB-FEAT-366 — tier 3. The compiled server against a REAL repository (this one, cloned so a
# lane can be claimed in it), the built client in front of it, and a lane whose agent actually
# speaks — so the words line is read off a screen rather than out of a fixture.
#
# `run-gate.sh` does this against `gate/fixture-repo`, nine synthetic documents and a stand-in
# agent that prints nothing, which is exactly why WEB-FIX-248 and WEB-FIX-261 say a green gate is
# not tier 3. The lane here is claimed through `E12` and released through `E13`, and the agent it
# spawns replays a COMMITTED CAPTURE OF THE REAL CLI byte for byte — the same recordings
# `server/tests/test_lane.py`'s SRV-FEAT-454 cases feed, so the words on screen are words the
# real program produced.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLIENT="$(dirname "$HERE")"
ROOT="$(cd "$CLIENT/../.." && pwd)"
BINARY="$ROOT/.cache/server/build/factory-server"
CAPTURE="$ROOT/server/tests/fixtures/cli-stream-2026-07-27-tools.jsonl"
OUT="${1:-$HERE/.tier3-366}"

free_port() { python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()'; }
API_PORT="${API_PORT:-$(free_port)}"
WEB_PORT="${WEB_PORT:-$(free_port)}"

rm -rf "$OUT"; mkdir -p "$OUT"

cleanup() {
  if [ -n "${LANE:-}" ]; then
    curl -s -X DELETE "http://127.0.0.1:$API_PORT/api/lanes/${LANE//\//%2F}?force=true" >/dev/null 2>&1 || true
  fi
  [ -n "${PREVIEW_PID:-}" ] && kill "$PREVIEW_PID" 2>/dev/null || true
  [ -n "${SERVER_PID:-}" ] && kill "$SERVER_PID" 2>/dev/null || true
  wait 2>/dev/null || true
  # THE CLONE GOES, AND THE EVIDENCE STAYS.
  #
  # It is 433 ticket documents, 152 reports and an ESCAPES ledger sitting inside this worktree,
  # and `server/tests/ -m repository` sweeps the FILESYSTEM rather than git — so a clone left
  # behind joins the corpus and the document gate starts reporting this repository's own history
  # back to itself as new offences. Measured: leaving it added
  # `test_no_document_instructs_an_agent_to_use_a_har_ticket_prefix` to the failing set, with
  # every hit a path inside `.tier3-366/repo`. Gitignored is not the same as invisible.
  #
  # The shots, the logs and the server's own output are NOT removed: they are what the report
  # quotes and what QA re-reads.
  [ -n "${OUT:-}" ] && rm -rf "$OUT/repo" "$OUT/agent" "$OUT/agent-config" 2>/dev/null || true
}
trap cleanup EXIT

# ── a real repository, and one a lane may be claimed in ───────────────────────────────────────
# THIS repository's documents — 300-odd tickets, the epics, the reports, SPEC.md, MODEL.md — and
# not nine synthetic ones. It is a clone rather than the checkout itself because claiming a lane
# writes a worktree, and a lane worktree's git metadata directory is bound read-only inside the
# sandbox this runs in: the tool would be refused halfway through E12 for a reason that has
# nothing to do with what is being driven.
echo "── cloning this repository so a lane can be claimed in it ─────────────────────"
git clone --quiet --no-hardlinks "$ROOT" "$OUT/repo"
( cd "$OUT/repo" && git checkout --quiet -b tier3-366 )
echo "  $(find "$OUT/repo/docs/tickets" -name '*.md' | wc -l) ticket documents"

# ── the stand-in agent, replaying a committed capture of the real CLI ─────────────────────────
# NOT under /tmp: `lane/store.cpp` puts the agent binary's directory into `hostReadOnly` and
# `confine.cpp` binds it BEFORE `--tmpfs /tmp`, so a bind under /tmp is a bind nothing can see
# (SRV-FIX-137, and the same paragraph in `run-gate.sh`).
#
# It prints the capture's own lines, in the capture's own order, and then holds the lane open.
# It invents no frame: a stand-in written by the session that wrote the code cannot disagree with
# it, so every byte it emits came off `claude 2.1.92` and is committed at the path below.
#
# The gap between lines is REAL TIME and it is five seconds, because §1's sentence is *the line
# MOVES while the agent works*. A replay that finishes before the browser opens leaves that clause
# asserted against a line that was already final, which is a fact about a fixture and not about a
# screen. The server coalesces M18 to one frame per 2 s, so five seconds is one frame per block.
mkdir -p "$OUT/agent"
cp "$CAPTURE" "$OUT/agent/capture.jsonl"
cat > "$OUT/agent/claude" <<'STANDIN'
#!/bin/sh
# Replays the committed capture line by line, then stays alive so the lane keeps its process.
while IFS= read -r line; do
  printf '%s\n' "$line"
  sleep 5
done < "$(dirname "$0")/capture.jsonl"
exec sleep 900
STANDIN
chmod 755 "$OUT/agent/claude"

# ── where the lane's agent configuration directory is built ───────────────────────────────────
# `lane/store.cpp:155` roots it at `/tmp` unless `FACTORY_AGENT_CONFIG_ROOT` says otherwise, and
# `/tmp` inside a lane is read-only — so `E18` answers `409 confinement_unavailable` and no lane
# in this repository can start a process at all. It is the same read-only `/tmp` that stops the
# browser gate taking its lock, and both are filed as `WEB-FIX-376`.
mkdir -p "$OUT/agent-config"
AGENT_CONFIG_ROOT="$(cd "$OUT/agent-config" && pwd -P)"

# ── and the directory the manifest declares the agent's credentials live in ───────────────────
# `manifest/store.cpp:676` refuses the whole manifest with `agent_credentials_missing` when
# `agent.credentials_dir` names nothing — deliberately, so an agent does not discover mid-run that
# it cannot authenticate. A lane's HOME is a fresh tmpfs and the real credentials are bound at
# `$CLAUDE_CONFIG_DIR` instead, so the declared path does not exist and E12 answers 409 for a
# reason that has nothing to do with this ticket. The directory is created, empty: the stand-in
# agent below authenticates against nothing and needs no secret.
mkdir -p "${HOME:?}/.claude"

echo
echo "── the compiled server, against the clone ─────────────────────────────────────"
FACTORY_HTTP_PORT="$API_PORT" PATH="$OUT/agent:$PATH" \
  FACTORY_AGENT_CONFIG_ROOT="$AGENT_CONFIG_ROOT" \
  "$BINARY" --repo "$OUT/repo" > "$OUT/server.log" 2>&1 &
SERVER_PID=$!
for _ in $(seq 1 80); do
  sleep 0.25
  HEALTH="$(curl -s "http://127.0.0.1:$API_PORT/api/health" 2>/dev/null || true)"
  [ -n "$HEALTH" ] && break
done
[ -n "${HEALTH:-}" ] || { echo "server never answered"; tail -30 "$OUT/server.log"; exit 1; }
echo "$HEALTH"

# The identity check `run-gate.sh` makes, for its reason: a page served against the wrong
# repository looks perfectly correct.
EXPECTED="$(cd "$OUT/repo" && pwd -P)"
echo "$HEALTH" | grep -q "\"root\":\"$EXPECTED\"" || {
  echo "WRONG SERVER: this process is not serving $EXPECTED"; exit 2; }
echo "identity checked: serving $EXPECTED"

echo
echo "── the built bundle, served by vite preview ───────────────────────────────────"
( cd "$CLIENT" && FACTORY_API_PORT="$API_PORT" FACTORY_WEB_PORT="$WEB_PORT" \
    npx vite preview > "$OUT/preview.log" 2>&1 ) &
PREVIEW_PID=$!
for _ in $(seq 1 60); do
  curl -sf "http://127.0.0.1:$WEB_PORT/" > /dev/null 2>&1 && break
  sleep 0.5
done
curl -sf "http://127.0.0.1:$WEB_PORT/" > /dev/null || { echo "preview never answered"; exit 1; }
echo "  http://127.0.0.1:$WEB_PORT"

echo
echo "── a lane, and a run in it (E12, then E18) ────────────────────────────────────"
TICKET="${TICKET:-WEB-FEAT-366}"
curl -s -X POST "http://127.0.0.1:$API_PORT/api/lanes" \
  -H 'Content-Type: application/json' \
  -d "{\"ticket_id\":\"$TICKET\",\"workspace\":\"factory-client\"}" > "$OUT/claim.json"
LANE="$(python3 -c 'import json;print(json.load(open("'"$OUT"'/claim.json")).get("lane",""))')"
[ -n "$LANE" ] || { echo "E12 claimed nothing:"; cat "$OUT/claim.json"; echo;
                    tail -30 "$OUT/server.log"; exit 1; }
echo "  claimed $LANE for $TICKET"

curl -s -X POST "http://127.0.0.1:$API_PORT/api/lanes/${LANE//\//%2F}/runs" \
  -H 'Content-Type: application/json' -d '{}' > "$OUT/run.json"
python3 -c 'import json;d=json.load(open("'"$OUT"'/run.json"));print("  run",d.get("run_id"),"pid",d.get("pid"))'

echo
echo "── E9, once the agent has spoken ──────────────────────────────────────────────"
python3 - "$API_PORT" "$LANE" <<'PY'
import json, sys, time, urllib.request
port, lane = sys.argv[1], sys.argv[2]
deadline = time.time() + 30
said = None
while time.time() < deadline:
    rows = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/lanes"))["lanes"]
    row = next((r for r in rows if r["lane"] == lane), None)
    if row is None:
        time.sleep(0.25); continue
    assert "last_words" in row, f"E9 has no last_words member at all: {row}"
    if row["last_words"] is not None:
        said = row["last_words"]; break
    time.sleep(0.25)
print("  E9 last_words:", json.dumps(said))
if said is None:
    print("  the agent said nothing within 30s — the surfaces below will show the ABSENCE case")
PY

echo
echo "── the browser ────────────────────────────────────────────────────────────────"
BASE="http://127.0.0.1:$WEB_PORT" API="http://127.0.0.1:$API_PORT" LANE="$LANE" \
  TICKET="$TICKET" OUT="$OUT" node "$HERE/tier3-web-feat-366.mjs"
