#!/usr/bin/env bash
# Turn the guest image into a root filesystem for Firecracker (design section 2).
#
#   docker create + docker export of fleetkit-guest:dev, the tree patched so that
#   /etc/resolv.conf is a symlink to /run/resolv.conf (BuildKit bind-mounts resolv.conf
#   during builds, so the Dockerfile cannot do it), then mkfs.ext4 -d inside the pinned
#   alpine e2fsprogs container. No buildx, no loop devices, no root on the calling host.
#
# Usage:  images/guest/build-rootfs.sh
# Env:    ARCH      amd64 | arm64   (default: the Docker engine's architecture)
#         IMAGE     source image     (default: fleetkit-guest:dev)
#         OUT_DIR   output directory (default: build/, relative to the repository root)
#         LOCK_ENV  pin file         (default: images/lock.env; inline pins if absent)
#
# Produces  $OUT_DIR/guest-<arch>.ext4  and  $OUT_DIR/guest-<arch>.manifest.json
# (base digest, image id, rootfs sha256 and size, package list).

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"

LOCK_ENV="${LOCK_ENV:-$repo_root/images/lock.env}"
if [ -f "$LOCK_ENV" ]; then
    # shellcheck disable=SC1090
    . "$LOCK_ENV"
fi
ALPINE_IMAGE="${ALPINE_IMAGE:-alpine:3.22@sha256:5291449c3df73caf6ed85e649dec1b9e818b39a5d8c871e97afc13e9cd5e8fa8}"
DEBIAN_IMAGE="${DEBIAN_IMAGE:-debian:bookworm-slim@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251}"

IMAGE="${IMAGE:-fleetkit-guest:dev}"
ARCH="${ARCH:-$(docker version -f '{{.Server.Arch}}')}"
OUT_DIR="${OUT_DIR:-$repo_root/build}"

case "$ARCH" in
    amd64|arm64) ;;
    x86_64) ARCH=amd64 ;;
    aarch64) ARCH=arm64 ;;
    *) echo "build-rootfs: unsupported ARCH '$ARCH' (amd64 or arm64)" >&2; exit 2 ;;
esac

log() { echo "build-rootfs: $*" >&2; }

image_arch="$(docker image inspect -f '{{.Architecture}}' "$IMAGE" 2>/dev/null || true)"
if [ -z "$image_arch" ]; then
    log "image $IMAGE not found; build it first (make guest-image)"
    exit 1
fi
if [ "$image_arch" != "$ARCH" ]; then
    log "image $IMAGE is $image_arch but ARCH=$ARCH; build the image for the target architecture"
    exit 1
fi
image_id="$(docker image inspect -f '{{.Id}}' "$IMAGE")"

mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"
work="$OUT_DIR/.work-$ARCH"
rm -rf "$work"
mkdir -p "$work"
trap 'rm -rf "$work"' EXIT

ext4_name="guest-$ARCH.ext4"
manifest="$OUT_DIR/guest-$ARCH.manifest.json"

log "exporting $IMAGE ($image_id)"
cid="$(docker create --label fleetkit.role=build "$IMAGE")"
docker export "$cid" > "$work/rootfs.tar"
docker rm -f "$cid" > /dev/null
log "exported $(du -h "$work/rootfs.tar" | cut -f1) tar"

log "packing $ext4_name in $ALPINE_IMAGE"
docker run --rm --label fleetkit.role=build \
    -v "$OUT_DIR:/out" \
    -e "ARCH=$ARCH" \
    "$ALPINE_IMAGE" sh -eu -c '
        apk add --no-cache e2fsprogs > /dev/null
        mkdir /rootfs
        tar -C /rootfs -xf "/out/.work-$ARCH/rootfs.tar"
        # The patch the Dockerfile cannot make: resolv.conf lives on tmpfs, filled by init.
        rm -f /rootfs/etc/resolv.conf
        ln -s /run/resolv.conf /rootfs/etc/resolv.conf
        rm -f /rootfs/.dockerenv
        # Mount points init needs; docker export may omit the empty ones.
        mkdir -p /rootfs/proc /rootfs/sys /rootfs/dev /rootfs/run /rootfs/tmp
        chmod 1777 /rootfs/tmp
        test -x /rootfs/sbin/init
        test -f /rootfs/etc/fleetkit-packages.txt
        used_mib=$(du -sm /rootfs | cut -f1)
        size_mib=$((used_mib + used_mib / 5 + 64))
        rm -f "/out/$1"
        mkfs.ext4 -q -F -L fleetkit-guest -E root_owner=0:0 -d /rootfs "/out/$1" "${size_mib}M"
        e2fsck -fn "/out/$1" > /dev/null
        cp /rootfs/etc/fleetkit-packages.txt "/out/.work-$ARCH/packages.txt"
        sha256sum "/out/$1" | cut -d" " -f1 > "/out/.work-$ARCH/sha256"
        echo "$size_mib" > "/out/.work-$ARCH/size_mib"
    ' sh "$ext4_name"

sha256="$(cat "$work/sha256")"
size_mib="$(cat "$work/size_mib")"
size_bytes="$(wc -c < "$OUT_DIR/$ext4_name" | tr -d ' ')"
chromium_version="$(grep -E '^chromium/' "$work/packages.txt" | awk '{print $2}' | head -n1)"

python3 - "$manifest" "$work/packages.txt" <<EOF
import json, sys, datetime
manifest_path, packages_path = sys.argv[1], sys.argv[2]
packages = []
with open(packages_path, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("Listing"):
            continue
        name_repo, _, rest = line.partition(" ")
        name = name_repo.split("/", 1)[0]
        fields = rest.split()
        packages.append({"name": name, "version": fields[0] if fields else "", "arch": fields[1] if len(fields) > 1 else ""})
doc = {
    "arch": "$ARCH",
    "image": "$IMAGE",
    "image_id": "$image_id",
    "base_image": "$DEBIAN_IMAGE",
    "packer_image": "$ALPINE_IMAGE",
    "rootfs": {"path": "$ext4_name", "sha256": "$sha256", "size_bytes": int("$size_bytes"), "fs_size_mib": int("$size_mib"), "label": "fleetkit-guest"},
    "chromium_version": "$chromium_version",
    "package_count": len(packages),
    "packages": packages,
    "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
}
with open(manifest_path, "w", encoding="utf-8") as f:
    json.dump(doc, f, indent=1)
    f.write("\n")
EOF

log "wrote $OUT_DIR/$ext4_name ($size_bytes bytes, sha256 $sha256)"
log "wrote $manifest (chromium $chromium_version)"
