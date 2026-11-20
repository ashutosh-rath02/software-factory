#!/bin/bash
set -eo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

BUILD_DIR="$SCRIPT_DIR/../.cache/server/build"

case "${1:-build}" in
  build)
      cmake -S . -B "$BUILD_DIR" -DCMAKE_BUILD_TYPE=Release >/dev/null
      cmake --build "$BUILD_DIR" -j"$(nproc)"
      echo "[+] factory-server built at $BUILD_DIR/factory-server"
      ;;
  run)
      "$SCRIPT_DIR/build.sh" build
      # The repository is given, not discovered. Default to the one this checkout sits in,
      # because that is the one the developer typing this means.
      REPO="${2:-$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)}"
      echo "[+] pointed at $REPO"
      exec "$BUILD_DIR/factory-server" --repo "$REPO"
      ;;
  clean)
      rm -rf "$BUILD_DIR"
      echo "[+] cleaned"
      ;;
  *)
      echo "usage: ./build.sh [build|run [repo-root]|clean]" >&2
      exit 2
      ;;
esac
