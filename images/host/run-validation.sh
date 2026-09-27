#!/bin/bash
# Drives one validation run on the experiment host from a workstation, using SSM
# Run Command only (no Session Manager plugin needed). Every stage syncs the host's
# evidence to the results bucket and pulls it locally, and the host is torn down
# from an exit trap whatever happens. Run with bash, not zsh (arrays, word splitting).
#
#   bash images/host/run-validation.sh <run-id> [--skip-apply]
#
# Prerequisites: infra/experiments applied at least once (bucket, role, SG exist),
# backend.hcl and terraform.tfvars in place, the management profile logged in.
# The host apply itself is gated: the script prints the plan and applies it only if
# the plan is exactly "1 to add" for the host; the teardown plan must be exactly
# "1 to destroy". Anything else stops the script and leaves state for a human.
set -uo pipefail

RUN_ID=${1:?run id}
SKIP_APPLY=${2:-}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
STACK="$REPO/infra/experiments"
OUT="$REPO/results/$RUN_ID"
LOG="$OUT/run-validation.log"
mkdir -p "$OUT"
A=(aws --profile fleetkit --region us-east-1)
HARD_STOP_LOCAL=${HARD_STOP_LOCAL:-09:30}   # all AWS work stops here; teardown runs

log() { printf '%s %s\n' "$(date '+%H:%M:%S')" "$*" | tee -a "$LOG"; }
session_ok() { aws sts get-caller-identity --output text >/dev/null 2>&1 && "${A[@]}" sts get-caller-identity --output text >/dev/null 2>&1; }
past_hard_stop() { [ "$(date '+%H:%M')" \> "$HARD_STOP_LOCAL" ]; }

teardown() {
  log "teardown: planning host_count=0"
  cd "$STACK" || return
  terraform plan -input=false -no-color -var host_count=0 -out=tfplan-down >"$OUT/teardown-plan.txt" 2>&1
  if grep -qE '^Plan: 0 to add, 0 to change, 1 to destroy' "$OUT/teardown-plan.txt" && grep -q 'aws_instance.host\[0\] will be destroyed' "$OUT/teardown-plan.txt"; then
    terraform apply -input=false -no-color tfplan-down >>"$OUT/teardown-plan.txt" 2>&1 && log "teardown: host destroyed" || log "teardown: APPLY FAILED, see teardown-plan.txt"
  elif grep -qE '^No changes' "$OUT/teardown-plan.txt"; then
    log "teardown: nothing to destroy"
  else
    log "teardown: UNEXPECTED PLAN, not applying; terminating tagged instances directly"
    IDS=$("${A[@]}" ec2 describe-instances --filters Name=tag:Project,Values=fleetkit Name=instance-state-name,Values=pending,running,stopping,stopped --query 'Reservations[].Instances[].InstanceId' --output text)
    [ -n "$IDS" ] && "${A[@]}" ec2 terminate-instances --instance-ids $IDS --output text | tee -a "$LOG"
  fi
  "${A[@]}" ec2 describe-instances --filters Name=tag:Project,Values=fleetkit --query 'Reservations[].Instances[].[InstanceId,State.Name]' --output text | tee -a "$LOG"
}
trap teardown EXIT

sync_evidence() {
  local stage=$1
  "${A[@]}" s3 sync "s3://$BUCKET/runs/$RUN_ID" "$OUT/host" --quiet 2>>"$LOG" && log "evidence synced after $stage ($(find "$OUT/host" -type f | wc -l | tr -d ' ') files)"
}

# run <label> <timeout-seconds> <shell command on the host>; output saved to $OUT/<label>.txt
run() {
  local label=$1 to=$2 cmd=$3
  if ! session_ok; then log "AWS session expired before $label; stopping"; exit 2; fi
  if past_hard_stop; then log "past hard stop before $label; stopping"; exit 3; fi
  log "run: $label (timeout ${to}s)"
  local cid st
  cid=$("${A[@]}" ssm send-command --document-name AWS-RunShellScript --instance-ids "$IID" \
        --parameters "commands=[\"$cmd\"],executionTimeout=[\"$to\"]" \
        --output-s3-bucket-name "$BUCKET" --output-s3-key-prefix "runs/$RUN_ID/ssm/$label" \
        --query Command.CommandId --output text) || { log "send-command failed"; return 1; }
  for _ in $(seq 1 $(( to / 10 + 6 ))); do
    sleep 10
    st=$("${A[@]}" ssm get-command-invocation --command-id "$cid" --instance-id "$IID" --query Status --output text 2>/dev/null)
    case "$st" in Success|Failed|TimedOut|Cancelled) break;; esac
  done
  "${A[@]}" ssm get-command-invocation --command-id "$cid" --instance-id "$IID" \
      --query '[StandardOutputContent,StandardErrorContent]' --output text > "$OUT/$label.txt" 2>&1
  log "run: $label -> $st ($(wc -l < "$OUT/$label.txt" | tr -d ' ') lines)"
  sync_evidence "$label"
  [ "$st" = "Success" ]
}

cd "$STACK" || exit 1
if ! session_ok; then log "AWS session not valid; aborting before apply"; trap - EXIT; exit 2; fi
BUCKET=$(terraform output -raw results_bucket)

if [ "$SKIP_APPLY" != "--skip-apply" ]; then
  log "apply: planning host_count=1"
  terraform plan -input=false -no-color -var host_count=1 -var repo_ref="$(git -C "$REPO" rev-parse HEAD)" -out=tfplan-up >"$OUT/apply-plan.txt" 2>&1
  if grep -qE '^Plan: 1 to add, 0 to change, 0 to destroy' "$OUT/apply-plan.txt" && grep -q 'aws_instance.host\[0\] will be created' "$OUT/apply-plan.txt"; then
    terraform apply -input=false -no-color tfplan-up >>"$OUT/apply-plan.txt" 2>&1 || { log "apply failed"; exit 1; }
    log "apply: host created"
  else
    log "apply: UNEXPECTED PLAN (see apply-plan.txt); not applying"; trap - EXIT; exit 1
  fi
fi
IID=$(terraform output -json host_instance_ids | python3 -c 'import json,sys; print(json.load(sys.stdin)[0])')
log "host: $IID (self-terminates 4 h after boot)"
echo "$IID" > "$OUT/instance-id"

log "waiting for SSM registration"
for _ in $(seq 1 40); do
  s=$("${A[@]}" ssm describe-instance-information --filters Key=InstanceIds,Values="$IID" --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null)
  [ "$s" = "Online" ] && break; sleep 15
done
[ "$s" = "Online" ] || { log "host never registered with SSM"; exit 1; }

run cloud-init 960 'timeout 900 cloud-init status --wait --long; echo EXIT=$?; cp /var/log/cloud-init-output.log /var/lib/fleetkit/ 2>/dev/null; true' || exit 1
run host-setup 1800 "cd /opt/fleetkit && git fetch -q && git checkout -q $(git -C "$REPO" rev-parse HEAD) && FLEETKIT_RUN_ID=$RUN_ID bash images/host/setup.sh 2>&1 | tail -80" || exit 1
run hostcheck 300 "FLEETKIT_RUN_ID=$RUN_ID bash /opt/fleetkit/images/host/hostcheck.sh 2>&1" || exit 1
run trial-n1 1200 "FLEETKIT_RUN_ID=$RUN_ID bash /opt/fleetkit/images/host/stage.sh trial-n1 2>&1 | tail -60" || log "trial-n1 did not succeed; continuing to collect evidence"
run trial-n24 1800 "FLEETKIT_RUN_ID=$RUN_ID bash /opt/fleetkit/images/host/stage.sh trial-n24 2>&1 | tail -60" || log "trial-n24 did not succeed; continuing"
run smoke 2400 "FLEETKIT_RUN_ID=$RUN_ID bash /opt/fleetkit/images/host/stage.sh smoke 2>&1 | tail -80" || log "smoke did not succeed; continuing"
run bundle 900 "FLEETKIT_RUN_ID=$RUN_ID bash /opt/fleetkit/images/host/stage.sh bundle 2>&1 | tail -40" || log "bundle reported problems; evidence synced anyway"
log "all stages attempted; tearing down"
exit 0
