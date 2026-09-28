#!/bin/bash
# One stage of a campaign run on the worker host, run over SSM Run Command as root by the
# launcher (experiments/launcher/launch.py):
#   stage.sh run | bundle
# The driver runs as a transient systemd unit so an SSM timeout can't kill it; the
# evidence directory is synced to the results bucket on every exit.
#
# Optional environment: FIXTURE_URL (fixture on the support host; default the bridge
# address 10.200.0.1:8081), OTLP_ENDPOINT (collector on the support host; without it
# the driver runs with --no-lgtm).
#
# The run stage carries out one run of a campaign (experiments/launcher/launch.py) from its
# spec, SPEC_FILE (default $OUT/spec.json), what experiments/schema/expand.py --run prints.
# FLEETKIT_RUN_ID is then <campaign>/<run>. Optional: SUPPORT_HEALTH_URL (the support host's
# health service, http://<ip>:8082), RUN_TIMEOUT_S (bound on the driver, default 7200).
set -uo pipefail
STAGE=${1:?stage}
cd /opt/fleetkit
FK=/var/lib/fleetkit
RUN_ID=${FLEETKIT_RUN_ID:?FLEETKIT_RUN_ID}
OUT="$FK/runs/$RUN_ID"; mkdir -p "$OUT"
IID=$(cat "$FK/instance-id")
BUCKET=$(cat "$FK/results-bucket")
PY=/opt/fleetkit/harness/.venv/bin/python
export PYTHONPATH=/opt/fleetkit/harness/host:/opt/fleetkit/harness/driver:/opt/fleetkit/harness/telemetry:/opt/fleetkit/harness/guest
FIX=${FIXTURE_URL:-http://10.200.0.1:8081}
if [ -n "${OTLP_ENDPOINT:-}" ]; then LGTM_ARGS=(--otlp-endpoint "$OTLP_ENDPOINT"); else LGTM_ARGS=(--no-lgtm); fi
GIT_COMMIT=$(git rev-parse HEAD 2>/dev/null || true)
t() { printf '%s %s\n' "$(date '+%H:%M:%S')" "$*"; }
imds() {
  local tok
  tok=$(curl -sfX PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60') \
    && curl -sf -H "X-aws-ec2-metadata-token: $tok" "http://169.254.169.254/latest/meta-data/$1"
}
sync_out() { aws s3 sync --quiet "$OUT" "s3://$BUCKET/runs/$RUN_ID/" 2>/dev/null && t "synced $OUT"; }
trap sync_out EXIT

# run_unit <name> <timeout-s> <cmd...>: transient unit, output piped back, bounded.
run_unit() {
  local name=$1 to=$2; shift 2
  systemctl reset-failed "fleetkit-$name" >/dev/null 2>&1 || true
  systemd-run --wait --pipe --collect --unit "fleetkit-$name" -p WorkingDirectory=/opt/fleetkit \
    -p "RuntimeMaxSec=$to" -E "PYTHONPATH=$PYTHONPATH" -E "FLEETKIT_HOST_ID=$IID" -E "FLEETKIT_GIT_COMMIT=$GIT_COMMIT" "$@"
}

# ensure_backend <name>: the host daemon runs the spec's hypervisor, restarted with it if not.
# A backend this host can't run keeps the daemon from starting, and the run from starting.
hostd_backend() { curl -fsS -m 5 http://127.0.0.1:8090/health 2>/dev/null | jq -r '.backend // empty' 2>/dev/null; }
ensure_backend() {
  local want=$1 have
  have=$(hostd_backend)
  [ "$have" = "$want" ] && return 0
  t "host daemon runs '${have:-nothing}', the spec asks for '$want': restarting it with --backend $want"
  sed -i -E "s/--backend [a-z-]+/--backend $want/" /etc/systemd/system/fleetkit-hostd.service
  systemctl daemon-reload && systemctl restart fleetkit-hostd
  for _ in $(seq 1 30); do
    [ "$(hostd_backend)" = "$want" ] && return 0
    sleep 1
  done
  t "THE HOST DAEMON CAN'T RUN $want HERE; not running"
  journalctl -u fleetkit-hostd -n 30 --no-pager 2>/dev/null
  return 1
}

summarize_trial() {
  local dir=$1
  [ -f "$dir/microvms.csv" ] || { t "no microvms.csv in $dir"; return; }
  $PY - "$dir" <<'EOF'
import csv, json, glob, os, sys, statistics as st
d = sys.argv[1]
microvms = list(csv.DictReader(open(os.path.join(d, "microvms.csv"))))
tasks = list(csv.DictReader(open(os.path.join(d, "tasks.csv")))) if os.path.exists(os.path.join(d, "tasks.csv")) else []
st_ms = [float(m["startup_ms"]) for m in microvms if m.get("startup_ms")]
ok = [t for t in tasks if t.get("ok") in ("True", "true", "1")]
print(f"microVMs {len(microvms)} outcomes {sorted(set(m['outcome'] for m in microvms))} startup_ms p50 {st.median(st_ms):.0f} max {max(st_ms):.0f}" if st_ms else f"microVMs {len(microvms)} no startup data")
if tasks:
    tm = [float(t["task_ms"]) for t in ok]
    print(f"tasks {len(tasks)} ok {len(ok)} categories {sorted(set(t['failure_category'] for t in tasks))}" + (f" task_ms p50 {st.median(tm):.0f} max {max(tm):.0f}" if tm else ""))
for tj in sorted(glob.glob(os.path.join(d, "*trial*.json")) + glob.glob(os.path.join(d, "trials", "*.json"))):
    try:
        j = json.load(open(tj)); print(os.path.basename(tj), "status", j.get("status"), "passed", j.get("passed"))
    except Exception as e: print(tj, "unreadable", e)
EOF
}

case "$STAGE" in
  run)
    SPEC=${SPEC_FILE:-$OUT/spec.json}
    [ -s "$SPEC" ] || { t "SPEC MISSING: $SPEC"; exit 2; }
    want=$(jq -r '(.spec // .).worker_host.instance_type // empty' "$SPEC")
    hv=$(jq -r '(.spec // .).hypervisor.name // empty' "$SPEC")
    ITYPE=$(imds instance-type)
    if [ -z "$want" ] || [ "$ITYPE" != "$want" ]; then
      t "INSTANCE TYPE MISMATCH: this host is '${ITYPE:-unknown}', the spec asks for '${want:-nothing}'; not running"
      exit 2
    fi
    ensure_backend "$hv" || exit 2
    period=$(curl -fsS -m 5 http://127.0.0.1:8090/host/info 2>/dev/null | jq -r '.metrics_period_s // empty' 2>/dev/null)
    [ "$period" = "0.2" ] || t "WARNING: the host daemon samples every ${period:-?} s; the driver samples five times a second"
    args=(--spec "$SPEC" --host-url http://127.0.0.1:8090 --fixture-check-url "$FIX" --fixture-base-url "$FIX"
          --host-id "$IID" "${LGTM_ARGS[@]}" --quiet)
    [ -n "${SUPPORT_HEALTH_URL:-}" ] && args+=(--support-health-url "$SUPPORT_HEALTH_URL")
    to=${RUN_TIMEOUT_S:-7200}
    t "run: $(jq -r '[.campaign, .run] | map(. // "?") | join("/")' "$SPEC") on $ITYPE, $hv, fixture $FIX, unit bound ${to}s"
    run_unit run "$to" "$PY" -m driver trial "${args[@]}" --out "$OUT/run"
    rc=$?; t "driver exit $rc"; summarize_trial "$OUT/run"
    jq -c '.plan | {stop_reason, complete, boundary, densities_not_run}' "$OUT/run/run.json" 2>/dev/null
    [ -s "$OUT/run/ops.jsonl" ] && { t "operational log:"; cat "$OUT/run/ops.jsonl"; }
    exit $rc ;;
  bundle)
    cp -f /var/log/cloud-init-output.log "$OUT/" 2>/dev/null
    cp -f "$FK/render-slot0.txt" "$OUT/" 2>/dev/null
    journalctl -u fleetkit-hostd --no-pager > "$OUT/hostd-journal.log" 2>/dev/null
    rc=0
    if [ -d "$OUT/run" ]; then
      # A campaign run: the criteria come from its run.json, the price from instance-types.json.
      ITYPE=$(imds instance-type)
      PRICE=$(jq -r --arg t "$ITYPE" '.types[$t].usd_per_hour // empty' experiments/schema/instance-types.json)
      t "report $OUT/run ($ITYPE at \$${PRICE:-?}/h)"
      "$PY" -m driver report --run "$OUT/run" --price-per-hour "${PRICE:?no price for $ITYPE}" --instance "$ITYPE" \
        --fixture-manifest fixture/dist/manifest.json --quiet || rc=1
      t "bundle $OUT/run"
      "$PY" -m driver bundle --run "$OUT/run" --aws --strict --hostd-dir "$FK/runs/hostd" --cloud-init-log /var/log/cloud-init-output.log \
        --hostcheck-output "$OUT/hostcheck.txt" --lock-env images/lock.env --guest-manifest "$FK/guest-amd64.manifest.json" \
        --instance-type "$ITYPE" || { t "bundle $OUT/run reported missing items"; rc=1; }
    fi
    curl -fsS -m 5 http://127.0.0.1:8090/host/verify-clean > "$OUT/verify-clean.json" 2>/dev/null; cat "$OUT/verify-clean.json"; echo
    exit $rc ;;
  *) echo "unknown stage $STAGE"; exit 2 ;;
esac
