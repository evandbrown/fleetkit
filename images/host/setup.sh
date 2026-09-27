#!/bin/bash
# Host setup after cloud-init, run over SSM Run Command as root from /opt/fleetkit.
# Idempotent: every step can be re-run after a fix. Does what cloud-init deliberately
# doesn't: the Python venv, the fixture, the guest image and rootfs, the fixture
# server on the bridge address, and the host daemon as a systemd unit.
set -euo pipefail
cd /opt/fleetkit
. images/lock.env
FK=/var/lib/fleetkit
mkdir -p "$FK/runs" /var/log/fleetkit
export PYTHONPATH=/opt/fleetkit/harness/host:/opt/fleetkit/harness/driver:/opt/fleetkit/harness/telemetry:/opt/fleetkit/harness/guest
t() { printf '%s %s\n' "$(date '+%H:%M:%S')" "$*"; }

command -v make >/dev/null 2>&1 || dnf -y -q install make jq >/dev/null
t "commit $(git rev-parse --short HEAD)"
TOKEN=$(curl -sfX PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60')
IID=$(curl -sf -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/instance-id)
echo "$IID" > "$FK/instance-id"; t "instance $IID"

t "venv (python3.12)"
make -s venv PYTHON3=python3.12 >/var/log/fleetkit/setup-venv.log 2>&1 || { tail -30 /var/log/fleetkit/setup-venv.log; exit 1; }
harness/.venv/bin/python --version

t "fixture"
make -s fixture PYTHON3=python3.12 | tail -3
test -f fixture/dist/manifest.json

t "guest image (amd64, native)"
make -s guest-image >/var/log/fleetkit/setup-image.log 2>&1 || { tail -40 /var/log/fleetkit/setup-image.log; exit 1; }
docker image inspect fleetkit-guest:dev --format 'image {{.Id}} {{.Architecture}} {{.Size}} bytes'

t "rootfs"
make -s rootfs >/var/log/fleetkit/setup-rootfs.log 2>&1 || { tail -40 /var/log/fleetkit/setup-rootfs.log; exit 1; }
ls -la build/
cp -f build/guest-amd64.ext4 "$FK/guest.ext4"
cp -f build/guest-amd64.manifest.json "$FK/guest-amd64.manifest.json"
sha256sum "$FK/guest.ext4" | cut -c1-64

t "fixture server on the bridge (10.200.0.1:8081)"
docker rm -f fleetkit-fixture >/dev/null 2>&1 || true
docker run -d --name fleetkit-fixture --restart unless-stopped --label fleetkit.role=fixture \
  -p 10.200.0.1:8081:80 -v /opt/fleetkit/fixture/dist:/usr/share/nginx/html:ro "$NGINX_IMAGE" >/dev/null
for _ in $(seq 1 20); do curl -fsS -m 2 -o /dev/null http://10.200.0.1:8081/ && break; sleep 1; done
curl -fsS -m 2 -o /dev/null -w 'fixture: HTTP %{http_code}\n' http://10.200.0.1:8081/index.html

t "host daemon unit"
cat > /etc/systemd/system/fleetkit-hostd.service <<EOF
[Unit]
Description=fleetkit host daemon (firecracker backend)
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/fleetkit
Environment=PYTHONPATH=$PYTHONPATH
Environment=FLEETKIT_HOST_ID=$IID
ExecStart=/opt/fleetkit/harness/.venv/bin/python -m hostd --backend firecracker --bind 127.0.0.1 --port 8090 --log-dir $FK/runs/hostd --no-otlp --host-id $IID
Restart=on-failure
RestartSec=2
KillSignal=SIGINT
TimeoutStopSec=60

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now fleetkit-hostd
for _ in $(seq 1 30); do curl -fsS -m 2 http://127.0.0.1:8090/health >/dev/null 2>&1 && break; sleep 1; done
curl -fsS -m 2 http://127.0.0.1:8090/health; echo
systemctl is-active fleetkit-hostd

t "render one session (dry run) for the record"
harness/.venv/bin/python -m hostd --backend firecracker --render --slot 0 2>&1 | head -40 > "$FK/render-slot0.txt" || true

touch "$FK/setup-done"
t "setup done"
