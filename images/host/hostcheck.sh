#!/bin/bash
# Host self-check before a validation run. Prints one RESULT line per check and
# exits non-zero if any is FAIL. Boots one microVM from the built guest rootfs on
# a tap and waits for the guest daemon's /health, then kills it and cleans up.
# Everything that can hang is under timeout. Run as root.
#
# The microVM boots with the hypervisor the spec names, through the same backend code
# the host daemon uses (hostd.backends.bootcheck): the spec's hypervisor section comes
# from SPEC (a resolved spec, or `expand.py --run` output) or HYPERVISOR (the section as
# JSON), else Firecracker with its defaults. Both hypervisors' versions are checked.
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
cloud-hypervisor --version 2>&1 | head -1
cloud-hypervisor --version 2>/dev/null | grep -q "${CLOUD_HYPERVISOR_VERSION:-v53.0}" && res CLOUD_HYPERVISOR PASS || res CLOUD_HYPERVISOR FAIL
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

if [ -n "${SPEC:-}" ]; then HYPERVISOR=$(jq -c '.spec.hypervisor // .hypervisor' "$SPEC"); fi
HYPERVISOR=${HYPERVISOR:-'{"name":"firecracker"}'}
echo "== one microVM to /health (slot 99: 10.200.0.109), hypervisor $HYPERVISOR =="
# bootcheck prints NAME_RESULT lines (HYPERVISOR_HOST, GUEST_HEALTH, GUEST_EGRESS, SAMPLE,
# NO_LEFTOVER_VM); the guest fetches the fixture itself for GUEST_EGRESS.
PY=/opt/fleetkit/harness/.venv/bin/python; [ -x "$PY" ] || PY=python3.12
BOOT="$EV/bootcheck"; seen=""
while IFS= read -r line; do
  case "$line" in
    *_RESULT=*) res "${line%%_RESULT=*}" "${line#*_RESULT=}"; seen="$seen ${line%%_RESULT=*}" ;;
    *) echo "$line" ;;
  esac
done < <(cd /opt/fleetkit/harness/host && timeout 300 "$PY" -m hostd.backends.bootcheck --hypervisor "$HYPERVISOR" \
  --slot 99 --timeout 90 --egress-url "$FIX/index.html" --run-root /run/fleetkit-check --log-dir "$BOOT" --out "$BOOT" 2>&1)
for check in GUEST_HEALTH GUEST_EGRESS NO_LEFTOVER_VM; do
  case "$seen " in *" $check "*) ;; *) res "$check" FAIL ;; esac
done
case "$seen " in *" NO_LEFTOVER_VM "*) ;; *)
  # bootcheck did not finish: remove what it may have left
  systemctl kill --signal=SIGKILL fc-vm99.scope ch-vm99.scope 2>/dev/null
  ip link del fc-99 2>/dev/null; ip link del ch-99 2>/dev/null; rm -rf /run/fleetkit-check ;;
esac
echo "console lines: $(wc -l < "$BOOT/console.log" 2>/dev/null || echo 0)"
grep -m3 -E 'Linux version|fleetkit-init|starting guestd|BusyBox|Kernel panic|attempted to kill init' "$BOOT/console.log" 2>/dev/null || true

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
