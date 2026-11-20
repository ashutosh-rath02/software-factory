#!/usr/bin/env bash
# WEB-FIX-333 — tier 3. The compiled server against THIS repository, the built client in front of
# it, and `tier3-web-fix-333.mjs` typing into the real search field.
#
# `run-gate.sh` does the same for `gate/fixture-repo`, a synthetic repository of nine tickets. §1
# is a request count over a catalog with no index, so it only means anything at the size the real
# backlog is — which is what this script points the server at.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLIENT="$(dirname "$HERE")"
ROOT="$(cd "$CLIENT/../.." && pwd)"
BINARY="$ROOT/.cache/server/build/factory-server"
API_PORT="${API_PORT:-7787}"
WEB_PORT="${WEB_PORT:-7789}"
OUT="${1:-$HERE/.tier3-333}"

rm -rf "$OUT"; mkdir -p "$OUT"

cleanup() {
  [ -n "${SERVER_PID:-}" ] && kill "$SERVER_PID" 2>/dev/null || true
  [ -n "${PREVIEW_PID:-}" ] && kill "$PREVIEW_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT

echo "── the compiled server, against $ROOT ─────────────────────────────────────────"
FACTORY_HTTP_PORT="$API_PORT" "$BINARY" --repo "$ROOT" > "$OUT/server.log" 2>&1 &
SERVER_PID=$!
for _ in $(seq 1 80); do
  sleep 0.25
  HEALTH="$(curl -s "http://127.0.0.1:$API_PORT/api/health" 2>/dev/null || true)"
  [ -n "$HEALTH" ] && break
done
[ -n "${HEALTH:-}" ] || { echo "server never answered"; tail -20 "$OUT/server.log"; exit 1; }
echo "$HEALTH"

# The identity check run-gate.sh makes, and for the same reason: a page served against the wrong
# repository looks perfectly correct. This one has to be THIS repository — the request count in §1
# is only a measurement at the size of the real backlog.
EXPECTED="$(cd "$ROOT" && pwd -P)"
echo "$HEALTH" | grep -q "\"root\":\"$EXPECTED\"" || {
  echo "WRONG SERVER: this process is not serving $EXPECTED"; exit 2; }
echo "identity checked: serving $EXPECTED"
echo

# How big the backlog under test actually is — the number that makes the request count matter.
echo "── the backlog this run measures against ──────────────────────────────────────"
curl -s "http://localhost:$API_PORT/api/tickets?provenance=all&stage=&q=&page=1" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(f"  {d[\"total\"]} tickets in the catalog, {d[\"page_size\"]} to a page, {d[\"pages\"]} pages")'
echo

echo "── the built client in front of it ────────────────────────────────────────────"
( cd "$CLIENT" && FACTORY_API_PORT="$API_PORT" FACTORY_WEB_PORT="$WEB_PORT" \
    npx vite preview > "$OUT/preview.log" 2>&1 ) &
PREVIEW_PID=$!
for _ in $(seq 1 60); do
  curl -sf "http://localhost:$WEB_PORT/" > /dev/null 2>&1 && break
  sleep 0.5
done
echo "  http://localhost:$WEB_PORT"
echo

echo "── driving it ─────────────────────────────────────────────────────────────────"
node "$HERE/tier3-web-fix-333.mjs" "http://localhost:$WEB_PORT" "$OUT"
