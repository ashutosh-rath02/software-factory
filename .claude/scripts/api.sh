#!/usr/bin/env bash
# Talk to the local factory server — SRV-FIX-351.
#
#   .claude/scripts/api.sh GET  /api/health
#   .claude/scripts/api.sh POST /api/tickets/ids '{"prefix":"SRV-FIX"}'
#   .claude/scripts/api.sh PROGRESS '{"stage":"GATE"}'   # to $FACTORY_PROGRESS_URL
#
# WHY THIS FILE EXISTS INSTEAD OF `curl` IN THE ALLOW LIST.
#
# §1 grants `curl` "against localhost … Nothing broader". `--allowedTools` cannot express that.
# Measured at the boundary, every host-scoped form is refused, because the `:` inside the rule
# breaks its own prefix parse:
#
#   Bash(curl -sS http://127.0.0.1:*)                       -> curl -sS http://127.0.0.1/  REFUSED
#   Bash(curl -sS http://localhost:*)                       -> REFUSED
#   Bash(curl -sS http://127.0.0.1:7777/api/health)         -> REFUSED
#   Bash(curl -sS -X POST http://127.0.0.1:7777/api/…:*)    -> REFUSED
#
# Only `Bash(curl:*)` works, and that reaches ANY host — measured by SRV-FIX-351's QA round 2
# against 192.0.2.1, which the session was allowed to run. So the grant could be either unusable
# or unbounded, and neither is what §1 says.
#
# The bound therefore moves out of the rule syntax and into the program: the host is not a
# parameter here, so `Bash(.claude/scripts/api.sh:*)` grants localhost and cannot grant anything
# else. Same shape as `start-qa.sh` and `run-gate.sh`, which are already granted this way.
set -euo pipefail

HOST="http://127.0.0.1:${FACTORY_PORT:-7777}"

METHOD="${1:?usage: api.sh <GET|POST> <path> [json-body]}"
RAW_PATH="${2:?usage: api.sh <GET|POST> <path> [json-body]}"
BODY="${3:-}"

# `PROGRESS` posts to the URL the FACTORY put in the environment when it started this lane. The
# agent never names the host, which is the same property as above reached a different way — the
# prompts in `.factory/prompts/` used to write this call as a bare `curl`, and a bare `curl` is
# exactly the rule §1 forbids.
if [ "$METHOD" = "PROGRESS" ]; then
  : "${FACTORY_PROGRESS_URL:?api.sh PROGRESS: no FACTORY_PROGRESS_URL in the environment}"
  case "$FACTORY_PROGRESS_URL" in
    http://127.0.0.1:*|http://localhost:*) ;;
    *) echo "api.sh: FACTORY_PROGRESS_URL is not local: $FACTORY_PROGRESS_URL" >&2; exit 2 ;;
  esac
  exec curl -sS --max-time 30 -X POST "$FACTORY_PROGRESS_URL" \
       -H 'Content-Type: application/json' -d "${2:-{\}}"
fi

case "$METHOD" in
  GET|POST) ;;
  *) echo "api.sh: method must be GET, POST or PROGRESS, not '$METHOD'" >&2; exit 2 ;;
esac

# A path, never a URL. Anything with a scheme or an authority is a caller trying to reach past
# this file, which is the one thing it exists to prevent.
case "$RAW_PATH" in
  /*) ;;
  *)  echo "api.sh: path must start with '/', not '$RAW_PATH'" >&2; exit 2 ;;
esac
case "$RAW_PATH" in
  //*|*://*) echo "api.sh: '$RAW_PATH' is a URL, not a path. This script talks to $HOST only." >&2
             exit 2 ;;
esac

if [ "$METHOD" = "GET" ]; then
  exec curl -sS --max-time 30 "$HOST$RAW_PATH"
fi
exec curl -sS --max-time 30 -X POST "$HOST$RAW_PATH" \
     -H 'Content-Type: application/json' -d "${BODY:-{\}}"
