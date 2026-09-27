# Guest image: one source for both backends (docs/harness-design.md, section 2).
#
# The Docker backend runs this image directly; the Firecracker backend turns the same
# build into a root filesystem with build-rootfs.sh (docker create + docker export +
# mkfs.ext4 -d), so everything a microVM needs (init, tini, resolv.conf layout) is
# baked in here too.
#
# Build from the repository root so the guest daemon package is in the context:
#   docker build -f images/guest/Dockerfile -t fleetkit-guest:dev .
# Pass --build-arg DEBIAN_IMAGE=... from images/lock.env to override the pin below.

ARG DEBIAN_IMAGE=debian:bookworm-slim@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251
FROM ${DEBIAN_IMAGE}

ARG DEBIAN_FRONTEND=noninteractive

# Debian's real chromium package (same major on amd64 and arm64), python3-websockets
# from apt (10.x API on both architectures), tini as the pid-1 reaper, iproute2 for
# debugging a VM from its console, ca-certificates for the NAT smoke fetch.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      chromium \
      fonts-liberation \
      python3 \
      python3-websockets \
      tini \
      iproute2 \
      ca-certificates \
      procps \
 && rm -f /etc/chromium.d/apikeys /etc/chromium.d/extensions \
 && apt list --installed 2>/dev/null > /etc/fleetkit-packages.txt \
 && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*

# The guest daemon, importable as `guestd` without any PYTHONPATH so that
# `python3 -m guestd` works identically under Docker's entrypoint and the VM init.
COPY harness/guest/guestd /usr/lib/python3/dist-packages/guestd

# The microVM init (unused by the Docker backend, which enters through tini below).
# The systemd package (a dependency of chromium) leaves /usr/sbin/init as a symlink into
# systemd; a COPY onto it would write through the link, so the link goes first.
RUN rm -f /usr/sbin/init /sbin/init
COPY images/guest/init /sbin/init
RUN chmod 0755 /sbin/init \
 && rm -rf /usr/lib/python3/dist-packages/guestd/__pycache__ \
 && python3 -m compileall -q /usr/lib/python3/dist-packages/guestd \
 && mkdir -p /run /tmp && chmod 1777 /tmp

# Chromium writes its profile and caches under HOME; in a VM that is tmpfs.
ENV HOME=/tmp
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

# `docker run fleetkit-guest:dev --selftest` appends the flag to the module invocation.
ENTRYPOINT ["/usr/bin/tini", "-g", "--", "python3", "-m", "guestd"]
