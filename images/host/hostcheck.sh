#!/bin/bash
# Host self-check before a validation run. Prints one RESULT line per check and
# exits non-zero if any is FAIL. Boots one microVM from the built guest rootfs on
# a tap and waits for the guest daemon's /health, then kills it and cleans up.
# Everything that can hang is under timeout. Run as root.
#
# With FIXTURE_URL set (the support host's fixture) the fixture checks go there and
# FIXTURE_MATCH compares its manifest.json with the local build; with OTLP_ENDPOINT
# set, OTLP posts one span to the collector. GUEST_EGRESS always runs: the test VM
# fetches <fixture>/index.html itself through the guest daemon's /egress-check, which
# for a remote fixture goes through this host's NAT.
set -uo pipefail
. /opt/fleetkit/images/lock.env 2>/dev/null || true
FK=/var/lib/fleetkit
RUN_ID=${FLEETKIT_RUN_ID:-hostcheck}
EV="$FK/runs/$RUN_ID"; mkdir -p "$EV"
exec > >(tee "$EV/hostcheck.txt") 2>&1
BUCKET=$(cat "$FK/results-bucket" 2>/dev/null || true)
FIX=${FIXTURE_URL:-http://10.200.0.1:8081}
fail=0
res() { echo "$1_RESULT=$2"; [ "$2" = PASS ] || fail=1; }

echo "== kernel and virtualization =="; uname -r
[[ "$(uname -r)" == 6.18* ]] && res HOST_KERNEL PASS || res HOST_KERNEL FAIL
ls -l /dev/kvm 2>&1; [ -c /dev/kvm ] && res KVM PASS || res KVM FAIL
modprobe kvm_intel 2>/dev/null; lsmod | grep -q '^kvm_intel' && res KVM_INTEL PASS || res KVM_INTEL FAIL

echo "== binaries =="; firecracker --version 2>&1 | head -1
firecracker --version 2>/dev/null | grep -q "${FIRECRACKER_VERSION:-v1.17.0}" && res FIRECRACKER PASS || res FIRECRACKER FAIL
docker version --format '{{.Server.Version}}' 2>&1 && res DOCKER PASS || res DOCKER FAIL
python3.12 --version 2>&1 && res PYTHON PASS || res PYTHON FAIL
[ -x /opt/fleetkit/harness/.venv/bin/python ] && res VENV PASS || res VENV FAIL

echo "== artifacts =="
[ -f "$FK/vmlinux" ] && echo "${KERNEL_X86_64_SHA256:-}  $FK/vmlinux" | sha256sum -c --quiet 2>/dev/null && res KERNEL PASS || res KERNEL FAIL
[ -s "$FK/guest.ext4" ] && res ROOTFS PASS || res ROOTFS FAIL
docker image inspect fleetkit-guest:dev >/dev/null 2>&1 && res GUEST_IMAGE PASS || res GUEST_IMAGE FAIL

echo "== networking =="
ip -br addr show fcbr0 2>&1; ip -br addr show fcbr0 2>/dev/null | grep -q 10.200.0.1 && res BRIDGE PASS || res BRIDGE FAIL
iptables -S DOCKER-USER 2>/dev/null | grep -q 'fcbr0' && res FORWARD_RULES PASS || res FORWARD_RULES FAIL
iptables -t nat -S POSTROUTING 2>/dev/null | grep -q '10.200.0.0/24' && res NAT_RULE PASS || res NAT_RULE FAIL
[ "$(sysctl -n net.ipv4.ip_forward)" = 1 ] && res IP_FORWARD PASS || res IP_FORWARD FAIL
echo "fixture: $FIX"; curl -fsS -m 5 -o /dev/null "$FIX/" && res FIXTURE PASS || res FIXTURE FAIL
if [ -n "${FIXTURE_URL:-}" ]; then
  curl -fsS -m 5 "$FIXTURE_URL/manifest.json" 2>/dev/null | cmp -s - /opt/fleetkit/fixture/dist/manifest.json \
    && res FIXTURE_MATCH PASS || res FIXTURE_MATCH FAIL
fi
if [ -n "${OTLP_ENDPOINT:-}" ]; then
  # One minimal OTLP/JSON span; the collector answers 200 when it accepts it.
  now=$(date +%s%N)
  span='{"resourceSpans":[{"resource":{"attributes":[{"key":"service.name","value":{"stringValue":"hostcheck"}},{"key":"fleetkit.run_id","value":{"stringValue":"'"$RUN_ID"'"}}]},"scopeSpans":[{"scope":{"name":"hostcheck"},"spans":[{"traceId":"'"$(od -An -tx1 -N16 /dev/urandom | tr -d ' \n')"'","spanId":"'"$(od -An -tx1 -N8 /dev/urandom | tr -d ' \n')"'","name":"hostcheck","kind":1,"startTimeUnixNano":"'"$now"'","endTimeUnixNano":"'"$now"'"}]}]}]}'
  code=$(printf '%s' "$span" | curl -sS -m 5 -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' --data-binary @- "$OTLP_ENDPOINT/v1/traces" 2>&1)
  echo "otlp: POST $OTLP_ENDPOINT/v1/traces -> $code"
  [ "$code" = 200 ] && res OTLP PASS || res OTLP FAIL
fi

echo "== services =="
systemctl is-active fleetkit-hostd 2>&1; systemctl is-active --quiet fleetkit-hostd && curl -fsS -m 5 http://127.0.0.1:8090/health >/dev/null && res HOSTD PASS || res HOSTD FAIL

echo "== one microVM to /health (slot 99: 10.200.0.109) =="
TAP=fc-check; GIP=10.200.0.109; MAC=06:00:0A:C8:00:6D
ip link del "$TAP" 2>/dev/null; ip tuntap add dev "$TAP" mode tap && ip link set "$TAP" master fcbr0 && ip link set "$TAP" up
cat > "$EV/hostcheck-vm.json" <<EOF
{"boot-source":{"kernel_image_path":"$FK/vmlinux","boot_args":"console=ttyS0 reboot=k panic=1 ip=$GIP::10.200.0.1:255.255.255.0:vmcheck:eth0:off:10.42.0.2 init=/sbin/init"},
 "drives":[{"drive_id":"rootfs","path_on_host":"$FK/guest.ext4","is_root_device":true,"is_read_only":true}],
 "machine-config":{"vcpu_count":2,"mem_size_mib":2048,"smt":false},
 "network-interfaces":[{"iface_id":"eth0","guest_mac":"$MAC","host_dev_name":"$TAP"}]}
EOF
rm -f /run/fleetkit-check.sock
firecracker --api-sock /run/fleetkit-check.sock --config-file "$EV/hostcheck-vm.json" > "$EV/hostcheck-console.log" 2>&1 &
FCPID=$!
( sleep 150; kill -9 "$FCPID" 2>/dev/null ) & WATCHDOG=$!
t0=$(date +%s); ready=FAIL
for _ in $(seq 1 90); do
  if curl -fsS -m 2 "http://$GIP:8080/health" > "$EV/hostcheck-health.json" 2>/dev/null; then ready=PASS; break; fi
  kill -0 $FCPID 2>/dev/null || break
  sleep 1
done
echo "guest /health after $(( $(date +%s) - t0 )) s: $(cat "$EV/hostcheck-health.json" 2>/dev/null)"
res GUEST_HEALTH $ready
if [ "$ready" = PASS ]; then
  # The guest fetches the fixture itself (guest daemon, 5 s timeout); PASS needs ok and HTTP 200.
  curl -fsS -m 20 -G --data-urlencode "url=$FIX/index.html" "http://$GIP:8080/egress-check" > "$EV/hostcheck-egress.json" 2>/dev/null
  echo "guest egress-check: $(cat "$EV/hostcheck-egress.json" 2>/dev/null)"
  jq -e '.ok == true and .status == 200' "$EV/hostcheck-egress.json" >/dev/null 2>&1 && res GUEST_EGRESS PASS || res GUEST_EGRESS FAIL
else
  res GUEST_EGRESS FAIL
fi
kill -9 "$FCPID" 2>/dev/null; wait "$FCPID" 2>/dev/null; kill "$WATCHDOG" 2>/dev/null
pkill -9 -f 'fleetkit-check.sock' 2>/dev/null; ip link del "$TAP" 2>/dev/null; rm -f /run/fleetkit-check.sock
sleep 1; if pgrep -a firecracker >/dev/null; then pgrep -a firecracker; res NO_LEFTOVER_VM FAIL; else res NO_LEFTOVER_VM PASS; fi
echo "console lines: $(wc -l < "$EV/hostcheck-console.log")"; grep -m3 -E 'Linux version|fleetkit-init|starting guestd|BusyBox|Kernel panic|attempted to kill init' "$EV/hostcheck-console.log" || true

echo "== results bucket =="
if [ -n "$BUCKET" ]; then
  { echo "hostcheck $(date -u +%FT%TZ)"; } > "$EV/hostcheck-marker.txt"
  aws s3 cp --quiet "$EV/hostcheck-marker.txt" "s3://$BUCKET/runs/$RUN_ID/hostcheck-marker.txt" && res S3_WRITE PASS || res S3_WRITE FAIL
  aws s3 sync --quiet "$EV" "s3://$BUCKET/runs/$RUN_ID/" || true
else
  res S3_WRITE FAIL
fi
echo "HOSTCHECK_OVERALL=$([ $fail = 0 ] && echo PASS || echo FAIL)"
exit $fail
