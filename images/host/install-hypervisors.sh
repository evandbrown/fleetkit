#!/bin/bash
# Install both hypervisors pinned in images/lock.env into /usr/local/bin: Firecracker (release
# tarball, checked against its published checksum and the pin) and Cloud Hypervisor (the static
# release binary, checked against the pin). Every worker host gets both, so hosts are set up the
# same whichever hypervisor the spec names. Idempotent: a binary already at the pinned version is
# kept. x86_64 and aarch64. Run as root.
#
# Usage: images/host/install-hypervisors.sh [lock.env]
# Env:   BIN_DIR  where the binaries go (default /usr/local/bin)
set -euo pipefail
LOCK=${1:-"$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/lock.env"}
. "$LOCK"
BIN_DIR=${BIN_DIR:-/usr/local/bin}

case "$(uname -m)" in
  x86_64)  ARCH=x86_64;  FC_SHA=$FIRECRACKER_X86_64_SHA256;  CH_ASSET=cloud-hypervisor-static;         CH_SHA=$CLOUD_HYPERVISOR_X86_64_SHA256 ;;
  aarch64) ARCH=aarch64; FC_SHA=$FIRECRACKER_AARCH64_SHA256; CH_ASSET=cloud-hypervisor-static-aarch64; CH_SHA=$CLOUD_HYPERVISOR_AARCH64_SHA256 ;;
  *) echo "install-hypervisors: unsupported architecture $(uname -m)" >&2; exit 2 ;;
esac
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
fetch() { curl -fsSL --retry 5 --retry-delay 5 -o "$1" "$2"; }

if "$BIN_DIR/firecracker" --version 2>/dev/null | head -1 | grep -qwF "$FIRECRACKER_VERSION"; then
  echo "firecracker: $FIRECRACKER_VERSION already installed"
else
  base=https://github.com/firecracker-microvm/firecracker/releases/download/$FIRECRACKER_VERSION
  tgz=firecracker-$FIRECRACKER_VERSION-$ARCH.tgz
  fetch "$work/$tgz" "$base/$tgz"
  fetch "$work/$tgz.sha256.txt" "$base/$tgz.sha256.txt"
  (cd "$work" && sha256sum -c "$tgz.sha256.txt")
  echo "$FC_SHA  $work/$tgz" | sha256sum -c -
  tar -xzf "$work/$tgz" -C "$work"
  install -m 0755 "$work/release-$FIRECRACKER_VERSION-$ARCH/firecracker-$FIRECRACKER_VERSION-$ARCH" "$BIN_DIR/firecracker"
fi
"$BIN_DIR/firecracker" --version | head -1

if "$BIN_DIR/cloud-hypervisor" --version 2>/dev/null | head -1 | grep -qwF "$CLOUD_HYPERVISOR_VERSION"; then
  echo "cloud-hypervisor: $CLOUD_HYPERVISOR_VERSION already installed"
else
  fetch "$work/$CH_ASSET" "https://github.com/cloud-hypervisor/cloud-hypervisor/releases/download/$CLOUD_HYPERVISOR_VERSION/$CH_ASSET"
  echo "$CH_SHA  $work/$CH_ASSET" | sha256sum -c -
  install -m 0755 "$work/$CH_ASSET" "$BIN_DIR/cloud-hypervisor"
fi
"$BIN_DIR/cloud-hypervisor" --version | head -1
