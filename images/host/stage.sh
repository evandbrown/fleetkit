#!/bin/bash
# One validation stage on the host, run over SSM Run Command as root:
#   stage.sh trial-n1 | trial-n24 | smoke | capacity | bundle
# Each driver invocation runs as a transient systemd unit so an SSM timeout can't
# kill it; the evidence directory is synced to the results bucket on every exit.
#
# Optional environment: FIXTURE_URL (fixture on the support host; default the bridge
# address 10.200.0.1:8081), OTLP_ENDPOINT (collector on the support host; without it
# the driver runs with --no-lgtm), CAPACITY_CONFIG (capacity stage: which
# experiments/capacity/<name>.env to run, default baseline).
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
COMMON=(--backend firecracker --host-url http://127.0.0.1:8090 --fixture-check-url "$FIX" --fixture-base-url "$FIX" --host-id "$IID" "${LGTM_ARGS[@]}" --quiet)
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
if st_ms: open(os.path.join(os.path.dirname(d.rstrip('/')), "startup_ms_p50"), "w").write(f"{st.median(st_ms):.0f}\n")
EOF
}

case "$STAGE" in
  trial-n1)
    t "trial at density 1 (ready timeout 90 s)"
    run_unit trial-n1 1000 "$PY" -m driver trial "${COMMON[@]}" --densities 1 --trials-per-density 1 --ready-timeout-s 90 --out "$OUT/trial-n1"
    rc=$?; t "driver exit $rc"; summarize_trial "$OUT/trial-n1"; exit $rc ;;
  trial-n24)
    p50=$(cat "$OUT/startup_ms_p50" 2>/dev/null || echo 20000)
    rt=$(( p50 * 3 / 1000 )); [ "$rt" -lt 30 ] && rt=30; [ "$rt" -gt 180 ] && rt=180
    t "trials at densities 2 and 4 (ready timeout ${rt}s = 3x observed startup)"
    run_unit trial-n24 1600 "$PY" -m driver trial "${COMMON[@]}" --densities 2,4 --trials-per-density 1 --ready-timeout-s "$rt" --out "$OUT/trial-n24"
    rc=$?; t "driver exit $rc"; summarize_trial "$OUT/trial-n24"; exit $rc ;;
  smoke)
    p50=$(cat "$OUT/startup_ms_p50" 2>/dev/null || echo 20000)
    rt=$(( p50 * 3 / 1000 )); [ "$rt" -lt 30 ] && rt=30; [ "$rt" -gt 180 ] && rt=180
    t "smoke (one green round wanted, at most 2 rounds)"
    run_unit smoke 2300 "$PY" -m driver smoke "${COMMON[@]}" --densities 2,4 --ready-timeout-s "$rt" --hostd-dir "$FK/runs/hostd" --until-green 1 --max-rounds 2 --out "$OUT/smoke"
    rc=$?; t "driver exit $rc"; sed -n '1,40p' "$OUT/smoke/smoke-report.md" 2>/dev/null; exit $rc ;;
  capacity)
    # One driver invocation is one run: it tests the spec's densities in order (design: docs/capacity-experiment.md).
    CFG=experiments/capacity/${CAPACITY_CONFIG:-baseline}.env
    [ -f "$CFG" ] || { t "CAPACITY CONFIG MISSING: $CFG"; exit 2; }
    # shellcheck disable=SC1090
    . "$CFG"
    for v in DENSITIES INSTANCE_TYPE_EXPECTED PRICE_PER_HOUR; do
      [ -n "${!v:-}" ] || { t "CAPACITY CONFIG INCOMPLETE: $CFG does not set $v"; exit 2; }
    done
    ITYPE=$(imds instance-type)
    if [ "$ITYPE" != "$INSTANCE_TYPE_EXPECTED" ]; then
      t "INSTANCE TYPE MISMATCH: this host is '${ITYPE:-unknown}', $CFG expects '$INSTANCE_TYPE_EXPECTED'; not running"
      exit 2
    fi
    cp -f "$CFG" "$OUT/capacity.env"   # what the bundle stage reads back (price) and what ran
    hz_period=$(curl -fsS -m 5 http://127.0.0.1:8090/host/info 2>/dev/null | jq -r '.metrics_period_s // empty' 2>/dev/null)
    if [ -n "$hz_period" ] && [ -n "${METRICS_HZ:-}" ] && ! awk -v p="$hz_period" -v hz="$METRICS_HZ" 'BEGIN { d = p * hz - 1; exit !(d < 0.01 && d > -0.01) }'; then
      t "WARNING: host daemon samples every $hz_period s but METRICS_HZ=$METRICS_HZ; rows are deduplicated by ts"
    fi
    args=(--densities "$DENSITIES" --trials-per-density "${TRIALS_PER_DENSITY:-1}" --stop-at-first-miss)
    opt() { [ -n "${2:-}" ] && args+=("$1" "$2"); return 0; }
    opt --boundary-trials "${BOUNDARY_TRIALS:-}"
    opt --warmup "${WARMUP:-}"
    opt --settle-s "${SETTLE_S:-}"
    opt --metrics-hz "${METRICS_HZ:-}"
    opt --sample-interval-ms "${SAMPLE_INTERVAL_MS:-}"
    opt --step-p50-target-ms "${STEP_P50_TARGET_MS:-}"
    opt --step-p95-target-ms "${STEP_P95_TARGET_MS:-}"
    opt --task-p95-target-ms "${TASK_P95_TARGET_MS:-}"
    opt --ready-timeout-s "${READY_TIMEOUT_S:-}"
    opt --step-timeout-ms "${STEP_TIMEOUT_MS:-}"
    opt --task-timeout-ms "${TASK_TIMEOUT_MS:-}"
    opt --vcpus "${VCPUS:-}"
    opt --mem-mib "${MEM_MIB:-}"
    [ "${ILLUSTRATION:-0}" = 1 ] && args+=(--illustration)
    to=${CAPACITY_TIMEOUT_S:-7200}
    t "capacity: $CFG on $ITYPE, fixture $FIX, ${LGTM_ARGS[*]}, unit bound ${to}s"
    t "driver trial ${args[*]}"
    run_unit capacity "$to" "$PY" -m driver trial "${COMMON[@]}" "${args[@]}" --out "$OUT/capacity"
    rc=$?; t "driver exit $rc"; summarize_trial "$OUT/capacity"
    jq -c '.plan // empty' "$OUT/capacity/run.json" 2>/dev/null
    exit $rc ;;
  bundle)
    cp -f /var/log/cloud-init-output.log "$OUT/" 2>/dev/null
    cp -f "$FK/render-slot0.txt" "$OUT/" 2>/dev/null
    journalctl -u fleetkit-hostd --no-pager > "$OUT/hostd-journal.log" 2>/dev/null
    rc=0
    for d in "$OUT"/trial-n1 "$OUT"/trial-n24 "$OUT"/smoke; do
      [ -d "$d" ] || continue
      t "report $d"
      "$PY" -m driver report --run "$d" --price-per-hour 0.2117 --instance m8i.xlarge --step-target-ms 1000 --step-p95-ms 2000 --fixture-manifest fixture/dist/manifest.json --quiet || rc=1
      t "bundle $d"
      "$PY" -m driver bundle --run "$d" --aws --strict --hostd-dir "$FK/runs/hostd" --cloud-init-log /var/log/cloud-init-output.log \
        --hostcheck-output "$OUT/hostcheck.txt" --lock-env images/lock.env --guest-manifest "$FK/guest-amd64.manifest.json" || { t "bundle $d reported missing items"; rc=1; }
    done
    if [ -d "$OUT/capacity" ]; then
      # The criteria come from the run's own run.json; the price from the config it ran with.
      # shellcheck disable=SC1091
      [ -f "$OUT/capacity.env" ] && . "$OUT/capacity.env"
      ITYPE=$(imds instance-type)
      t "report $OUT/capacity ($ITYPE at \$${PRICE_PER_HOUR:-?}/h)"
      "$PY" -m driver report --run "$OUT/capacity" --price-per-hour "${PRICE_PER_HOUR:?PRICE_PER_HOUR}" --instance "$ITYPE" \
        --fixture-manifest fixture/dist/manifest.json --quiet || rc=1
      t "bundle $OUT/capacity"
      "$PY" -m driver bundle --run "$OUT/capacity" --aws --strict --hostd-dir "$FK/runs/hostd" --cloud-init-log /var/log/cloud-init-output.log \
        --hostcheck-output "$OUT/hostcheck.txt" --lock-env images/lock.env --guest-manifest "$FK/guest-amd64.manifest.json" \
        --instance-type "$ITYPE" || { t "bundle $OUT/capacity reported missing items"; rc=1; }
    fi
    curl -fsS -m 5 http://127.0.0.1:8090/host/verify-clean > "$OUT/verify-clean.json" 2>/dev/null; cat "$OUT/verify-clean.json"; echo
    exit $rc ;;
  *) echo "unknown stage $STAGE"; exit 2 ;;
esac
