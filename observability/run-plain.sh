#!/usr/bin/env bash
# Fallback for hosts without docker compose (AL2023 has no compose package, design section 2):
# the same two containers, names, labels, ports, mounts and network as compose.yaml, with
# plain `docker run`. Usage: observability/run-plain.sh up|down [--no-lgtm]
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/.." && pwd)"
LGTM_IMAGE="grafana/otel-lgtm:0.34.0@sha256:b966ea107831d526d9eb8fe4d2d86c9e5731392fad9dce8296bcf2072031f07c"
NGINX_IMAGE="nginx:1.29-alpine@sha256:5616878291a2eed594aee8db4dade5878cf7edcb475e59193904b198d9b830de"
if [[ -f "$root/images/lock.env" ]]; then
  # shellcheck disable=SC1091
  source "$root/images/lock.env"
fi
FIXTURE_BIND="${FIXTURE_BIND:-127.0.0.1}"
cmd="${1:-up}"; shift || true
want_lgtm=1
[[ "${1:-}" == "--no-lgtm" ]] && want_lgtm=0

case "$cmd" in
  up)
    docker network inspect fleetkit >/dev/null 2>&1 || docker network create fleetkit >/dev/null
    mkdir -p "$root/results/dev/lgtm/data" "$root/results/dev/lgtm/otlp"
    if [[ $want_lgtm -eq 1 ]] && ! docker ps -q -f name='^fleetkit-lgtm$' | grep -q .; then
      docker rm -f fleetkit-lgtm >/dev/null 2>&1 || true
      docker run -d --name fleetkit-lgtm --label fleetkit.role=observability --network fleetkit \
        -p 127.0.0.1:3000:3000 -p 127.0.0.1:4317:4317 -p 127.0.0.1:4318:4318 \
        --memory 2g --stop-timeout 60 -e ENABLE_LOGS_OTELCOL=true \
        -v "$root/results/dev/lgtm/data:/data" -v "$root/results/dev/lgtm/otlp:/otlp" \
        -v "$here/otelcol-config.yaml:/otel-lgtm/otelcol-config.yaml:ro" \
        "$LGTM_IMAGE" >/dev/null
      echo "started fleetkit-lgtm"
    fi
    if ! docker ps -q -f name='^fleetkit-fixture$' | grep -q .; then
      docker rm -f fleetkit-fixture >/dev/null 2>&1 || true
      docker run -d --name fleetkit-fixture --label fleetkit.role=fixture --network fleetkit \
        -p "${FIXTURE_BIND}:8081:80" -v "$root/fixture/dist:/usr/share/nginx/html:ro" \
        "$NGINX_IMAGE" >/dev/null
      echo "started fleetkit-fixture on ${FIXTURE_BIND}:8081"
    fi
    if [[ $want_lgtm -eq 1 ]]; then
      for _ in $(seq 1 60); do
        docker exec fleetkit-lgtm test -f /tmp/ready 2>/dev/null && { echo "fleetkit-lgtm ready"; break; }
        sleep 3
      done
    fi
    ;;
  down)
    docker rm -f fleetkit-lgtm fleetkit-fixture >/dev/null 2>&1 || true
    echo "stopped fleetkit-lgtm fleetkit-fixture"
    ;;
  *) echo "usage: $0 up|down [--no-lgtm]" >&2; exit 2 ;;
esac
