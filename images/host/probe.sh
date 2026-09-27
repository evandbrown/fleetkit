#!/usr/bin/env bash
# Stage 0 probe for a worker host. Run as root over SSM Run Command once
# cloud-init has finished, for example:
#
#   aws ssm send-command --profile fleetkit --region us-east-1 \
#     --document-name AWS-RunShellScript --comment "stage 0 probe" \
#     --targets Key=tag:Name,Values=fleetkit-exp-host \
#     --parameters 'commands=["timeout 600 cloud-init status --wait >/dev/null; bash /opt/fleetkit/images/host/probe.sh"]'
#
# It reports the host (kernel, KVM, tools, bridge and NAT rules, packages), boots
# two Firecracker microVMs on the pinned guest kernel with Firecracker's own
# Ubuntu rootfs (one plain, one on a tap attached to fcbr0 that the host pings),
# and uploads both console logs and this summary to the results bucket. Every
# step that could hang runs under a timeout. Three lines carry the verdict and
# are always printed, whatever fails before them:
#
#   PROBE_RESULT=PASS|FAIL   kernel banner and a systemd line on the console
#   TAP_RESULT=PASS|FAIL     the guest on the tap answered ping
#   S3_RESULT=PASS|FAIL      the logs reached the results bucket
#
# The exit status is 0 only when all three are PASS, so the Run Command
# invocation shows as Failed otherwise. Everything printed is also written to
# /var/lib/fleetkit/probe-summary.txt.
set -uo pipefail

fleetkit_dir=/var/lib/fleetkit
summary=$fleetkit_dir/probe-summary.txt
console=$fleetkit_dir/probe-console.log
console_tap=$fleetkit_dir/probe-console-tap.log
kernel=$fleetkit_dir/vmlinux
rootfs=$fleetkit_dir/ubuntu.squashfs
tap=tap-probe
guest_ip=10.200.0.10
export AWS_DEFAULT_REGION=${AWS_DEFAULT_REGION:-us-east-1}
export PATH=$PATH:/usr/local/bin:/usr/local/sbin:/usr/sbin:/sbin

# The pins (kernel bucket and prefix) come from images/lock.env, next to this
# script in the checkout, or from the standard checkout if run from elsewhere.
KERNEL_BASE_URL='' KERNEL_CI_PREFIX='' KERNEL_VERSION=''
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]:-}")" 2>/dev/null && pwd)
lock=${FLEETKIT_LOCK:-$script_dir/../lock.env}
[ -f "$lock" ] || lock=/opt/fleetkit/images/lock.env

mkdir -p "$fleetkit_dir"
: > "$summary"

# Everything printed also goes to the summary, one pipeline per line, so the
# file is complete at the moment it is uploaded.
say() { printf '%s\n' "$*" | tee -a "$summary"; }

# probe COMMAND [TIMEOUT]: show a shell snippet, run it under a timeout (default
# 60 s), print its output and, if it failed, its exit status. Never aborts.
probe() {
  {
    printf '\n$ %s\n' "$1"
    timeout -k 5 "${2:-60}" bash -c "$1" 2>&1 || echo "(exit $?)"
  } | tee -a "$summary"
}

say "=== fleetkit stage 0 probe on $(hostname) at $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
if [ -f "$lock" ]; then
  say "pins: $lock"
  # shellcheck disable=SC1090
  . "$lock"
else
  say "pins: $lock is missing, the rootfs download will fail"
fi

say ""
say "--- host ---"
probe 'uname -r'
probe 'ls -l /dev/kvm'
probe 'grep -c vmx /proc/cpuinfo'
probe 'modprobe kvm_intel; lsmod | grep kvm'
probe 'firecracker --version'
probe "docker version --format '{{.Server.Version}}'"
probe 'docker buildx version'
probe 'python3.12 --version'
probe 'iptables -S DOCKER-USER'
probe 'iptables -t nat -S POSTROUTING'
probe 'ip -br addr show fcbr0'
probe 'cloud-init status --long'
probe 'dnf list --installed docker iptables-nft e2fsprogs git python3.12 python3.12-pip 2>&1 | tail -n +2' 120

# ---------------------------------------------------------------------------
# Boot test 1: the pinned kernel and Firecracker's CI Ubuntu rootfs, no network.
# ---------------------------------------------------------------------------
say ""
say "--- boot test 1: plain microVM ---"
probe_result=FAIL
if [ ! -s "$rootfs" ]; then
  say "fetching Firecracker's CI rootfs (about 108 MB) to $rootfs"
  probe "curl -fsSL --retry 3 -o $rootfs.part $KERNEL_BASE_URL/$KERNEL_CI_PREFIX/x86_64/ubuntu-24.04.squashfs && mv $rootfs.part $rootfs" 300
fi

cat > "$fleetkit_dir/probe-vm.json" <<JSON
{
  "boot-source": {
    "kernel_image_path": "$kernel",
    "boot_args": "console=ttyS0 reboot=k panic=1"
  },
  "drives": [
    {
      "drive_id": "rootfs",
      "path_on_host": "$rootfs",
      "is_root_device": true,
      "is_read_only": true
    }
  ],
  "machine-config": {
    "vcpu_count": 1,
    "mem_size_mib": 512
  }
}
JSON

say "\$ timeout 45 firecracker --no-api --config-file $fleetkit_dir/probe-vm.json"
timeout -k 5 45 firecracker --no-api --config-file "$fleetkit_dir/probe-vm.json" \
  < /dev/null > "$console" 2>&1 || true
say "console: $(wc -l < "$console" | tr -d ' ') lines in $console"
say "$(grep -m2 -E 'Linux version|systemd\[1\]' "$console")"
if grep -q 'Linux version' "$console" && grep -q 'systemd\[1\]' "$console"; then
  probe_result=PASS
fi
say "PROBE_RESULT=$probe_result"

# ---------------------------------------------------------------------------
# Boot test 2: the same guest on a tap attached to fcbr0, static address from
# the kernel command line, pinged from the host across the bridge.
# ---------------------------------------------------------------------------
say ""
say "--- boot test 2: microVM on a tap attached to fcbr0 ---"
tap_result=FAIL
ip link del "$tap" 2>/dev/null || true
probe "ip tuntap add dev $tap mode tap && ip link set $tap master fcbr0 && ip link set $tap up && ip -br link show $tap"

cat > "$fleetkit_dir/probe-vm-tap.json" <<JSON
{
  "boot-source": {
    "kernel_image_path": "$kernel",
    "boot_args": "console=ttyS0 reboot=k panic=1 ip=$guest_ip::10.200.0.1:255.255.255.0:probe:eth0:off:10.42.0.2"
  },
  "drives": [
    {
      "drive_id": "rootfs",
      "path_on_host": "$rootfs",
      "is_root_device": true,
      "is_read_only": true
    }
  ],
  "network-interfaces": [
    {
      "iface_id": "eth0",
      "guest_mac": "06:00:0A:C8:00:0A",
      "host_dev_name": "$tap"
    }
  ],
  "machine-config": {
    "vcpu_count": 1,
    "mem_size_mib": 512
  }
}
JSON

say "\$ timeout 60 firecracker --no-api --config-file $fleetkit_dir/probe-vm-tap.json &"
timeout -k 5 60 firecracker --no-api --config-file "$fleetkit_dir/probe-vm-tap.json" \
  < /dev/null > "$console_tap" 2>&1 &
fc_pid=$!
sleep 20
say "\$ ping -c 3 -W 2 $guest_ip"
if timeout -k 5 30 ping -c 3 -W 2 "$guest_ip" 2>&1 | tee -a "$summary"; then
  tap_result=PASS
fi
say "TAP_RESULT=$tap_result"
wait "$fc_pid" || true
say "console: $(wc -l < "$console_tap" | tr -d ' ') lines in $console_tap"
say "$(grep -m3 -E 'Linux version|IP-Config|systemd\[1\]' "$console_tap")"
ip link del "$tap" 2>/dev/null || true

# ---------------------------------------------------------------------------
# Evidence to the results bucket, through the host's own role.
# ---------------------------------------------------------------------------
say ""
say "--- results bucket ---"
s3_result=FAIL
bucket=$(cat "$fleetkit_dir/results-bucket" 2>/dev/null || true)

# upload FILE: copy it under stage0/ in the bucket, reporting the outcome.
upload() {
  local file=$1 out rc
  out=$(timeout -k 5 120 aws s3 cp --only-show-errors "$file" "s3://$bucket/stage0/$(basename "$file")" 2>&1)
  rc=$?
  if [ "$rc" -eq 0 ]; then
    say "  $(basename "$file"): ok"
  else
    say "  $(basename "$file"): failed ($rc) $out"
  fi
  return "$rc"
}

if [ -z "$bucket" ]; then
  say "no bucket recorded in $fleetkit_dir/results-bucket"
else
  say "uploading to s3://$bucket/stage0/"
  uploaded=0
  for file in "$console" "$console_tap" "$summary"; do
    [ -f "$file" ] || continue
    upload "$file" && uploaded=$((uploaded + 1))
  done
  [ "$uploaded" -eq 3 ] && s3_result=PASS
fi
say "S3_RESULT=$s3_result"

# The copy in the bucket should carry the verdict lines too; best effort.
if [ "$s3_result" = PASS ]; then
  timeout -k 5 120 aws s3 cp --only-show-errors "$summary" "s3://$bucket/stage0/probe-summary.txt" >/dev/null 2>&1 || true
fi

[ "$probe_result" = PASS ] && [ "$tap_result" = PASS ] && [ "$s3_result" = PASS ]
