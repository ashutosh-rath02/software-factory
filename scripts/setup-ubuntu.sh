#!/bin/bash
set -e
export DEBIAN_FRONTEND=noninteractive
SUDO=""
[ "$(id -u)" -ne 0 ] && SUDO="sudo"
$SUDO apt-get update -y
$SUDO apt-get install -y build-essential cmake libboost-dev libssl-dev libgoogle-glog-dev pkg-config bubblewrap git curl python3 python3-pip ca-certificates
if ! command -v node >/dev/null 2>&1 || [ "$(node -p 'process.versions.node.split(".")[0]')" -lt 20 ]; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | $SUDO bash -
  $SUDO apt-get install -y nodejs
fi
echo "Dependencies installed. Run ./build.sh run"
