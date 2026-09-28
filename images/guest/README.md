# Guest image

One build for both backends (design section 2): the Docker backend runs the image; the
Firecracker backend boots a root filesystem packed from the same image.

| File | Role |
|---|---|
| `Dockerfile` | `debian:bookworm-slim` at the pinned digest + `chromium`, `fonts-liberation`, `python3`, `python3-websockets`, `tini`, `iproute2`, `ca-certificates`, `procps`; the guest daemon; the VM init |
| `init` | POSIX `/sbin/init` for the microVM: mounts, resolv.conf from `/proc/net/pnp`, `fleetkit.fault=` from the command line, `exec tini -g -- python3 -m guestd` |
| `build-rootfs.sh` | `docker create` + `docker export` + the resolv.conf symlink patch + `mkfs.ext4 -d` in the pinned alpine container → `build/guest-<arch>.ext4` and a manifest |

## Build and verify

```
# from the repository root (the build context must include harness/guest/guestd)
docker build -f images/guest/Dockerfile -t fleetkit-guest:dev .
docker run --rm fleetkit-guest:dev --selftest          # starts Chromium, runs the task path
```

The pin is the `DEBIAN_IMAGE` build argument, defaulting to the digest in this file;
`make guest-image` should pass `--build-arg DEBIAN_IMAGE=$(DEBIAN_IMAGE)` from
`images/lock.env` so one file holds every pin.

What the Dockerfile does beyond installing packages:

- removes `/etc/chromium.d/apikeys` and `/etc/chromium.d/extensions`. The daemon never runs
  Debian's `/usr/bin/chromium` wrapper anyway; it launches `/usr/lib/chromium/chromium`
  directly with the flag list from the design (plus any extra flags the spec adds, which
  `init` passes from the kernel command line to guestd).
- records `apt list --installed` into `/etc/fleetkit-packages.txt` (the manifest reads it).
- installs the daemon at `/usr/lib/python3/dist-packages/guestd`, so `python3 -m guestd`
  needs no `PYTHONPATH` under either entry point.
- removes the `/usr/sbin/init -> /lib/systemd/systemd` symlink that the `systemd` package
  (a chromium dependency) leaves behind before copying `init` to `/sbin/init`; a `COPY` onto
  the symlink would write through it.
- `ENTRYPOINT ["/usr/bin/tini", "-g", "--", "python3", "-m", "guestd"]`, `HOME=/tmp`,
  `EXPOSE 8080`.

Running it as the Docker backend does: `docker run -d -p 127.0.0.1:18080:8080
--network fleetkit --label fleetkit.role=microvm -e FLEETKIT_FAULT=... fleetkit-guest:dev`.

Measured on this Mac (arm64): image 829 MB, Chromium 154.0.8037.57, DevTools answering
about 150 ms after launch in a container, selftest task about 200 ms.

## Root filesystem for Firecracker

```
images/guest/build-rootfs.sh              # ARCH defaults to the engine's architecture
ARCH=amd64 images/guest/build-rootfs.sh   # on the AWS host
```

Steps, all through the Docker socket (no buildx, no loop devices, no root on the caller):

1. `docker create` + `docker export` of `fleetkit-guest:dev` (checked to match `ARCH`).
2. In `alpine:3.22` at the pinned digest, as root: `apk add e2fsprogs`, extract the tar,
   replace `/etc/resolv.conf` with a symlink to `/run/resolv.conf` (BuildKit bind-mounts
   `resolv.conf` during builds, so the Dockerfile cannot do this), drop `.dockerenv`, make
   sure `/proc /sys /dev /run /tmp` exist, then `mkfs.ext4 -F -L fleetkit-guest
   -E root_owner=0:0 -d <tree>` at the tree's size plus 20 % plus 64 MiB, then `e2fsck -fn`.
3. Write `build/guest-<arch>.manifest.json`: `arch`, `image`, `image_id`, `base_image`
   (the Debian digest), `packer_image` (the alpine digest), `rootfs {path, sha256,
   size_bytes, fs_size_mib, label}`, `chromium_version`, `packages [{name, version, arch}]`,
   `built_at`.

Outputs go to `build/` at the repository root (override with `OUT_DIR`); pins come from
`images/lock.env` when it exists (`DEBIAN_IMAGE`, `ALPINE_IMAGE`), else the inline values.
The `build/` directory holds a ~1 GiB file and should be gitignored.

Verified on this Mac: `guest-arm64.ext4`, 1,114,636,288 bytes, `e2fsck` clean, `/etc/resolv.conf
-> /run/resolv.conf`, `/sbin/init` a regular executable, the daemon and package list in
place. Booting it needs a Linux host with KVM; that is the AWS leg (design section 10).

## Boot contract for the host daemon

- Kernel: `vmlinux-6.18.48` from `images/lock.env`; root device `/dev/vda` (or `vda`
  read-only), `root=/dev/vda ro`, `console=ttyS0` on x86_64 / `console=ttyAMA0` on aarch64,
  `init=/sbin/init`, `ip=<guest>::<gateway>:<mask>:<hostname>:eth0:off:<dns>` from design
  section 3, and optionally `fleetkit.fault=<name>`.
- The root filesystem is read-only and shared; every VM writes only to tmpfs (`/run`,
  `/tmp`, `/dev/shm`). Chromium's profile is `/tmp/profile`.
- Console markers: `fleetkit-init: devtmpfs ok`, `proc ok`, `sysfs ok`, `devpts ok`,
  `tmpfs ok`, `resolv.conf ok`, `fault='...'`, `starting guestd`, then the daemon's JSON
  lines (`guestd listening`, `chromium launched`, `chromium ready`). On any failure:
  `fleetkit-init: FAILED: <step>` and a shell on the console.
- The daemon listens on `0.0.0.0:8080` and `/health` turns 200 when Chromium is up.
