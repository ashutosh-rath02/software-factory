#!/bin/bash
# The factory, both halves, one command.
#
#   ./build.sh run [repo-root]   build both, start both, open Chrome at http://localhost:7779
#   ./build.sh run --no-open     the same, without the browser (also: FACTORY_NO_BROWSER=1)
#   ./build.sh build             build both and stop
#   ./build.sh test              server suite + client suite
#   ./build.sh clean             remove both build directories
#
# The repository being worked on is GIVEN, never discovered — the server refuses to start
# without it. It defaults to the checkout this script sits in, because that is the one a
# developer typing `./build.sh run` means.
#
# Open 7779, not 7777: the client proxies /api and /ws to the server, so the browser sees one
# origin and CORS never applies. 7777 on its own serves JSON.
set -eo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

SERVER=server
CLIENT=client/web
BUILD_DIR=.cache/server/build
WEB_PORT="${FACTORY_WEB_PORT:-7779}"
API_PORT="${FACTORY_HTTP_PORT:-7777}"

# npm ci, but only when it would change something. package-lock.json moving is the signal —
# a stale node_modules is how `npm run dev` greets you with "markdown-it ... Are they
# installed?" after a pull that added a dependency.
client_deps() {
    local stamp="$CLIENT/node_modules/.factory-lock-stamp"
    if [ ! -d "$CLIENT/node_modules" ] || ! cmp -s "$CLIENT/package-lock.json" "$stamp"; then
        echo "[+] client dependencies"
        (cd "$CLIENT" && npm ci)
        cp "$CLIENT/package-lock.json" "$stamp"
    fi
}

case "${1:-build}" in
  build)
      "$SERVER/build.sh" build
      client_deps
      (cd "$CLIENT" && npm run build)
      echo "[+] both halves built"
      ;;

  run)
      # `--no-open` is a flag, not the repo-root: it may sit on either side of the path.
      NO_OPEN="${FACTORY_NO_BROWSER:-}"
      run_args=()
      for a in "${@:2}"; do
          case "$a" in
              --no-open) NO_OPEN=1 ;;
              *)         run_args+=("$a") ;;
          esac
      done
      REPO="${run_args[0]:-$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)}"
      "$SERVER/build.sh" build
      client_deps

      # Each child leads its own process group, and cleanup kills the GROUP.
      #
      # `npm run dev` is three processes deep — npm, then `sh -c vite`, then node — so killing
      # the pid bash hands back kills the wrapper and orphans the server that was actually
      # listening. It left a vite on 7779 with nothing behind it the first time this script ran.
      # SRV-FEAT-009 pre-decides the same rule for agents: kill the group, not the leader.
      pgids=()
      cleanup() {
          trap - EXIT INT TERM
          for g in "${pgids[@]}"; do kill -TERM -- "-$g" 2>/dev/null; done
          sleep 0.3
          for g in "${pgids[@]}"; do kill -KILL -- "-$g" 2>/dev/null; done
      }
      trap cleanup EXIT INT TERM

      # A previous run of THIS script is stopped for you; anything else is not.
      #
      # The check is the binary, not the port: `readlink /proc/<pid>/exe` against the server this
      # checkout just built. A stale copy of ours is a leftover and killing it is what you meant.
      # A different program — another checkout, another developer, something unrelated that happens
      # to want 7777 — is named and left alone, because a dev script that kills whatever is in its
      # way is a dev script that eventually kills the wrong thing.
      # `mine` is matched against the process's own binary AND its working directory: a server is
      # its exe, but a dev server is `node`, which every project on the machine also runs — so vite
      # is recognised by the directory it was started in, which is this checkout's client or nobody's.
      free_port() {
          local port="$1" label="$2" mine="$3" pid exe cwd
          for pid in $(ss -lptnH "sport = :$port" 2>/dev/null | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u); do
              exe="$(readlink -f "/proc/$pid/exe" 2>/dev/null)"
              cwd="$(readlink -f "/proc/$pid/cwd" 2>/dev/null)"
              if [ -n "$mine" ] && { [ "$exe" = "$mine" ] || [ "$cwd" = "$mine" ]; }; then
                  echo "[+] stopping the previous $label (pid $pid)"
                  # Every kill is `|| true`: under `set -e` a kill that finds the process already
                  # gone is a non-zero exit, and the script dies here having stopped the old server
                  # and started nothing — which is what it did the first time this was written.
                  kill "$pid" 2>/dev/null || true
                  for _ in 1 2 3 4 5 6 7 8 9 10; do
                      kill -0 "$pid" 2>/dev/null || break
                      sleep 0.3
                  done
                  kill -9 "$pid" 2>/dev/null || true
              else
                  echo "[!] :$port is held by something this script did not start — stop it first:" >&2
                  ss -lptn "sport = :$port" 2>/dev/null | grep LISTEN | sed 's/^/    /' >&2
                  [ -n "$exe" ] && echo "    its binary: $exe" >&2
                  exit 1
              fi
          done
      }

      # The browser waits for the port before it opens, because a Chrome started a beat early
      # greets you with ERR_CONNECTION_REFUSED and a reload you have to remember to do. It polls
      # for a LISTEN on the web port — the same evidence free_port reads — and gives up after 20s
      # rather than hanging the script on a client that failed to build.
      #
      # Deliberately NOT in pgids and disowned by its caller: pgids are killed on Ctrl-C, and the
      # window you are reading is not this script's to close. Staying out of the job table also
      # keeps the `wait -n` below meaning "one half exited" instead of "the browser opened".
      open_browser() {
          local url="http://localhost:$WEB_PORT" browser
          for _ in $(seq 1 100); do
              ss -lntH "sport = :$WEB_PORT" 2>/dev/null | grep -q LISTEN && break
              sleep 0.2
          done
          browser="$(command -v google-chrome || command -v google-chrome-stable \
                  || command -v chromium || command -v chromium-browser \
                  || command -v xdg-open)" || return 0
          setsid "$browser" "$url" >/dev/null 2>&1 &
      }

      free_port "$API_PORT" "server" "$(readlink -f "$BUILD_DIR/factory-server" 2>/dev/null)"
      free_port "$WEB_PORT" "dev server" "$(readlink -f "$CLIENT" 2>/dev/null)"

      echo "[+] pointed at $REPO"
      setsid "$BUILD_DIR/factory-server" --repo "$REPO" &
      pgids+=($!)

      # SRV-FIX-027 made the client refuse to guess which server it is for, so the port the
      # server just bound is handed to it rather than assumed on both sides.
      setsid bash -c "cd '$CLIENT' && FACTORY_API_PORT=$API_PORT FACTORY_WEB_PORT=$WEB_PORT exec npm run dev" &
      pgids+=($!)

      # No display is not a failure — it is a server, or a CI box, or an agent. It gets the URL.
      if [ -z "$NO_OPEN" ] && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
          echo "[+] opening http://localhost:$WEB_PORT   (Ctrl-C stops both)"
          open_browser & disown
      else
          echo "[+] open http://localhost:$WEB_PORT   (Ctrl-C stops both)"
      fi

      # Return as soon as EITHER exits rather than waiting for both: if the server dies, the
      # client is a shell showing stale data, and that is a worse report than stopping.
      # `|| true` because wait -n yields the child's exit code and `set -e` would take the
      # script down before it could say which half went.
      wait -n || true
      echo "[!] one half exited — stopping the other" >&2
      ;;

  test)
      (cd "$SERVER" && PYTHONPYCACHEPREFIX="$SCRIPT_DIR/.cache/python" python3 -m pytest tests/ -v)
      client_deps
      (cd "$CLIENT" && npm run test)
      ;;

  clean)
      "$SERVER/build.sh" clean
      rm -rf "$CLIENT/node_modules" "$CLIENT/dist"
      echo "[+] cleaned"
      ;;

  *)
      echo "usage: ./build.sh [run [repo-root] [--no-open]|build|test|clean]" >&2
      exit 2
      ;;
esac
