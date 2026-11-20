#!/usr/bin/env bash
# WEB-FIX-405 — tier 3. The compiled server against THIS repository, the built client in front of
# it, and a browser driven the way a person drives it. `run-gate.sh` drives `gate/fixture-repo`;
# this is the run that says the same things about the real backlog.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLIENT="$(dirname "$HERE")"
ROOT="$(cd "$CLIENT/../.." && pwd)"
BINARY="$ROOT/.cache/server/build/factory-server"
OUT="${1:-$HERE/.tier3-405}"

free_port() { python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()'; }
API_PORT="$(free_port)"
WEB_PORT="$(free_port)"

rm -rf "$OUT"; mkdir -p "$OUT/shots"

cleanup() {
  [ -n "${PREVIEW_PID:-}" ] && kill -- "-$PREVIEW_PID" 2>/dev/null || true
  [ -n "${SERVER_PID:-}" ] && kill -- "-$SERVER_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT

[ -x "$BINARY" ] || { echo "build the server first: cd server && ./build.sh build"; exit 2; }

echo "── the real C++ server, pointed at THIS repository ────────────────────────────"
set -m
FACTORY_HTTP_PORT="$API_PORT" FACTORY_AGENT_CONFIG_ROOT="$OUT/agent-config" \
  "$BINARY" --repo "$ROOT" > "$OUT/server.log" 2>&1 &
SERVER_PID=$!
set +m
for _ in $(seq 1 100); do
  sleep 0.1
  HEALTH="$(curl -s "http://127.0.0.1:$API_PORT/api/health" 2>/dev/null || true)"
  [ -n "$HEALTH" ] && break
done
echo "$HEALTH"

echo "── the BUILT bundle, served by vite preview ───────────────────────────────────"
set -m
( cd "$CLIENT" && FACTORY_API_PORT="$API_PORT" \
    npx vite preview --port "$WEB_PORT" --strictPort > "$OUT/preview.log" 2>&1 ) &
PREVIEW_PID=$!
set +m
for _ in $(seq 1 200); do
  sleep 0.1
  curl -sf "http://127.0.0.1:$WEB_PORT/" > /dev/null 2>&1 && break
done
echo "vite preview on 127.0.0.1:$WEB_PORT"

TIER3_WEB="http://127.0.0.1:$WEB_PORT" TIER3_API="http://127.0.0.1:$API_PORT" \
  TIER3_SHOTS="$OUT/shots" TIER3_REPORTS="$ROOT/docs/reports" \
  node "$HERE/tier3-web-fix-405.mjs"
