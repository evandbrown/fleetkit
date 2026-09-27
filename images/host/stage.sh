#!/bin/bash
# One validation stage on the host, run over SSM Run Command as root:
#   stage.sh trial-n1 | trial-n24 | smoke | bundle
# Each driver invocation runs as a transient systemd unit so an SSM timeout can't
# kill it; the evidence directory is synced to the results bucket on every exit.
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
FIX=http://10.200.0.1:8081
COMMON=(--backend firecracker --host-url http://127.0.0.1:8090 --fixture-check-url "$FIX" --fixture-base-url "$FIX" --host-id "$IID" --no-lgtm --quiet)
t() { printf '%s %s\n' "$(date '+%H:%M:%S')" "$*"; }
sync_out() { aws s3 sync --quiet "$OUT" "s3://$BUCKET/runs/$RUN_ID/" 2>/dev/null && t "synced $OUT"; }
trap sync_out EXIT

# run_unit <name> <timeout-s> <cmd...>: transient unit, output piped back, bounded.
run_unit() {
  local name=$1 to=$2; shift 2
  systemctl reset-failed "fleetkit-$name" >/dev/null 2>&1 || true
  systemd-run --wait --pipe --collect --unit "fleetkit-$name" -p WorkingDirectory=/opt/fleetkit \
    -p "RuntimeMaxSec=$to" -E "PYTHONPATH=$PYTHONPATH" -E "FLEETKIT_HOST_ID=$IID" "$@"
}

summarize_trial() {
  local dir=$1
  [ -f "$dir/sessions.csv" ] || { t "no sessions.csv in $dir"; return; }
  $PY - "$dir" <<'EOF'
import csv, json, glob, os, sys, statistics as st
d = sys.argv[1]
sess = list(csv.DictReader(open(os.path.join(d, "sessions.csv"))))
tasks = list(csv.DictReader(open(os.path.join(d, "tasks.csv")))) if os.path.exists(os.path.join(d, "tasks.csv")) else []
st_ms = [float(s["startup_ms"]) for s in sess if s.get("startup_ms")]
ok = [t for t in tasks if t.get("ok") in ("True", "true", "1")]
print(f"sessions {len(sess)} outcomes {sorted(set(s['outcome'] for s in sess))} startup_ms p50 {st.median(st_ms):.0f} max {max(st_ms):.0f}" if st_ms else f"sessions {len(sess)} no startup data")
if tasks:
    tm = [float(t["task_ms"]) for t in ok]
    print(f"tasks {len(tasks)} ok {len(ok)} categories {sorted(set(t['failure_category'] for t in tasks))}" + (f" task_ms p50 {st.median(tm):.0f} max {max(tm):.0f}" if tm else ""))
for tj in sorted(glob.glob(os.path.join(d, "*trial*.json")) + glob.glob(os.path.join(d, "trials", "*.json"))):
    try:
        j = json.load(open(tj)); print(os.path.basename(tj), "status", j.get("status"), "level_passed", j.get("level_passed"))
    except Exception as e: print(tj, "unreadable", e)
if st_ms: open(os.path.join(os.path.dirname(d.rstrip('/')), "startup_ms_p50"), "w").write(f"{st.median(st_ms):.0f}\n")
EOF
}

case "$STAGE" in
  trial-n1)
    t "trial n=1 (ready timeout 90 s)"
    run_unit trial-n1 1000 "$PY" -m driver trial "${COMMON[@]}" --n 1 --repeats 1 --ready-timeout-s 90 --out "$OUT/trial-n1"
    rc=$?; t "driver exit $rc"; summarize_trial "$OUT/trial-n1"; exit $rc ;;
  trial-n24)
    p50=$(cat "$OUT/startup_ms_p50" 2>/dev/null || echo 20000)
    rt=$(( p50 * 3 / 1000 )); [ "$rt" -lt 30 ] && rt=30; [ "$rt" -gt 180 ] && rt=180
    t "trial n=2,4 (ready timeout ${rt}s = 3x observed startup)"
    run_unit trial-n24 1600 "$PY" -m driver trial "${COMMON[@]}" --n 2,4 --repeats 1 --ready-timeout-s "$rt" --out "$OUT/trial-n24"
    rc=$?; t "driver exit $rc"; summarize_trial "$OUT/trial-n24"; exit $rc ;;
  smoke)
    p50=$(cat "$OUT/startup_ms_p50" 2>/dev/null || echo 20000)
    rt=$(( p50 * 3 / 1000 )); [ "$rt" -lt 30 ] && rt=30; [ "$rt" -gt 180 ] && rt=180
    t "smoke (one green run wanted, at most 2 attempts)"
    run_unit smoke 2300 "$PY" -m driver smoke "${COMMON[@]}" --levels 2,4 --ready-timeout-s "$rt" --hostd-dir "$FK/runs/hostd" --until-green 1 --max-runs 2 --out "$OUT/smoke"
    rc=$?; t "driver exit $rc"; sed -n '1,40p' "$OUT/smoke/smoke-report.md" 2>/dev/null; exit $rc ;;
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
    curl -fsS -m 5 http://127.0.0.1:8090/host/verify-clean > "$OUT/verify-clean.json" 2>/dev/null; cat "$OUT/verify-clean.json"; echo
    exit $rc ;;
  *) echo "unknown stage $STAGE"; exit 2 ;;
esac
