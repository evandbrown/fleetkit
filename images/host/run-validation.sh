#!/bin/bash
# Drives one run on the experiment hosts from a workstation, using SSM Run Command
# only (no Session Manager plugin needed). Every stage syncs the host's evidence to
# the results bucket and pulls it locally, and the hosts are torn down from an exit
# trap whatever happens. Run with bash, not zsh (arrays, word splitting).
#
#   bash images/host/run-validation.sh <run-id> [--skip-apply] [--plan validation|capacity] [--keep]
#
# Plans:
#   validation (default)  one m8i.xlarge host: cloud-init, host-setup, hostcheck,
#                         trial n=1, trial n=2,4, smoke, bundle.
#   capacity              one m8i.4xlarge host plus one support host (fixture and
#                         observability backend, images/support): cloud-init on both,
#                         host-setup and hostcheck against the support host, the
#                         capacity stage (experiments/capacity/${CAPACITY_CONFIG:-baseline}.env,
#                         bounded by CAPACITY_TIMEOUT_S, default 7200), bundle, then
#                         support-sync (the collector's files and support metrics to
#                         runs/<run-id>/support/ in the results bucket).
# --keep skips the teardown; the hosts' own shutdown timer (shutdown_after_minutes
# after boot) still ends them. The hard stop, after which no stage starts and a
# running one is cut short, is 4 hours after this script starts, or the next
# HARD_STOP_LOCAL=HH:MM (local time) when that is set.
#
# Prerequisites: infra/experiments applied at least once with the current code
# (bucket, role, both security groups exist: a plain apply with host_count=0
# support_count=0), backend.hcl and terraform.tfvars in place, the management
# profile logged in, and HEAD pushed (the hosts clone it from GitHub).
# The host apply itself is gated: the script prints the plan and applies it only if
# the plan is exactly the instances of the chosen plan ("1 to add", aws_instance.host[0];
# for capacity "2 to add", aws_instance.host[0] and aws_instance.support[0]) and
# nothing else. The teardown plans host_count=0 support_count=0 and must be exactly
# the destroy of some of those same instances (1 or 2). Anything else stops the
# script and leaves state for a human.
set -uo pipefail

RUN_ID=${1:?run id}; shift
SKIP_APPLY='' PLAN=validation KEEP=''
while [ $# -gt 0 ]; do
  case "$1" in
    --skip-apply) SKIP_APPLY=1 ;;
    --keep) KEEP=1 ;;
    --plan) PLAN=${2:-}; shift ;;
    --plan=*) PLAN=${1#--plan=} ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
case "$PLAN" in
  validation|capacity) ;;
  *) echo "--plan must be validation or capacity" >&2; exit 2 ;;
esac
REPO=$(cd "$(dirname "$0")/../.." && pwd)
STACK="$REPO/infra/experiments"
OUT="$REPO/results/$RUN_ID"
LOG="$OUT/run-validation.log"
mkdir -p "$OUT"
A=(aws --profile fleetkit --region us-east-1)
HEAD=$(git -C "$REPO" rev-parse HEAD)

# The instances this plan creates, and the only ones its gates accept.
if [ "$PLAN" = capacity ]; then
  APPLY_VARS=(-var host_count=1 -var support_count=1 -var instance_type=m8i.4xlarge)
  PLAN_ADDRS=("aws_instance.host[0]" "aws_instance.support[0]")
  CAPACITY_CONFIG=${CAPACITY_CONFIG:-baseline}
  CAPACITY_TIMEOUT_S=${CAPACITY_TIMEOUT_S:-7200}
else
  APPLY_VARS=(-var host_count=1)
  PLAN_ADDRS=("aws_instance.host[0]")
fi

# Hard stop: all AWS work stops here and the teardown runs. Not a fixed clock time.
START_EPOCH=$(date +%s)
if [ -n "${HARD_STOP_LOCAL:-}" ]; then
  [[ "$HARD_STOP_LOCAL" =~ ^[0-9]{1,2}:[0-9]{2}$ ]] || { echo "HARD_STOP_LOCAL must be HH:MM" >&2; exit 2; }
  HARD_STOP_EPOCH=$(date -j -f '%H:%M' "$HARD_STOP_LOCAL" +%s 2>/dev/null || date -d "$HARD_STOP_LOCAL" +%s 2>/dev/null) \
    || { echo "cannot parse HARD_STOP_LOCAL=$HARD_STOP_LOCAL" >&2; exit 2; }
  [ "$HARD_STOP_EPOCH" -gt "$START_EPOCH" ] || HARD_STOP_EPOCH=$(( HARD_STOP_EPOCH + 86400 ))
else
  HARD_STOP_EPOCH=$(( START_EPOCH + 4 * 3600 ))
fi
HARD_STOP_TEXT=$(date -r "$HARD_STOP_EPOCH" '+%F %H:%M' 2>/dev/null || date -d "@$HARD_STOP_EPOCH" '+%F %H:%M')

log() { printf '%s %s\n' "$(date '+%H:%M:%S')" "$*" | tee -a "$LOG"; }
session_ok() { aws sts get-caller-identity --output text >/dev/null 2>&1 && "${A[@]}" sts get-caller-identity --output text >/dev/null 2>&1; }
past_hard_stop() { [ "$(date +%s)" -ge "$HARD_STOP_EPOCH" ]; }

# changes <plan output>: one "<address> <action>" line per resource the plan changes
# ("aws_instance.host[0] created"); data sources read during apply are not changes.
changes() { sed -nE 's/^  # ([^ ]+) (will|must) be (.*)$/\1 \3/p' "$1" | grep -v ' read during apply$'; }

# apply_ok <plan output>: exactly this plan's instances created, nothing else.
apply_ok() {
  local want got
  grep -qE "^Plan: ${#PLAN_ADDRS[@]} to add, 0 to change, 0 to destroy\." "$1" || return 1
  want=$(printf '%s created\n' "${PLAN_ADDRS[@]}" | sort)
  got=$(changes "$1" | sort)
  [ "$want" = "$got" ]
}

# teardown_ok <plan output>: only destroys, 1 or more of this plan's instances, nothing else.
teardown_ok() {
  local n got line allowed
  n=$(sed -nE 's/^Plan: 0 to add, 0 to change, ([0-9]+) to destroy\..*$/\1/p' "$1")
  [ -n "$n" ] && [ "$n" -ge 1 ] && [ "$n" -le "${#PLAN_ADDRS[@]}" ] || return 1
  got=$(changes "$1")
  [ "$(printf '%s\n' "$got" | grep -c .)" -eq "$n" ] || return 1
  allowed=$(printf '%s destroyed\n' "${PLAN_ADDRS[@]}")
  while IFS= read -r line; do
    printf '%s\n' "$allowed" | grep -qxF -- "$line" || return 1
  done <<< "$got"
}

# support_sync: the support host's collector files and metrics to the bucket, at most once.
# Runs in a subshell with its own ten-minute budget, so neither the hard stop nor a failure
# inside it can stop the teardown that follows.
SUPPORT_SYNCED=''
support_sync() {
  [ "$PLAN" = capacity ] && [ -n "${SID:-}" ] && [ -z "$SUPPORT_SYNCED" ] || return 0
  SUPPORT_SYNCED=1
  ( HARD_STOP_EPOCH=$(( $(date +%s) + 660 ))
    run "$SID" support-sync 600 "FLEETKIT_RUN_ID=$RUN_ID bash /opt/fleetkit/images/support/sync.sh 2>&1" ) \
    || log "support-sync reported problems"
}

teardown() {
  support_sync
  log "teardown: planning host_count=0 support_count=0"
  cd "$STACK" || return
  terraform plan -input=false -no-color -var host_count=0 -var support_count=0 -out=tfplan-down >"$OUT/teardown-plan.txt" 2>&1
  if teardown_ok "$OUT/teardown-plan.txt"; then
    terraform apply -input=false -no-color tfplan-down >>"$OUT/teardown-plan.txt" 2>&1 && log "teardown: $(changes "$OUT/teardown-plan.txt" | tr '\n' ' ')" || log "teardown: APPLY FAILED, see teardown-plan.txt"
  elif grep -qE '^No changes' "$OUT/teardown-plan.txt"; then
    log "teardown: nothing to destroy"
  else
    log "teardown: UNEXPECTED PLAN, not applying; terminating tagged instances directly"
    IDS=$("${A[@]}" ec2 describe-instances --filters Name=tag:Project,Values=fleetkit Name=instance-state-name,Values=pending,running,stopping,stopped --query 'Reservations[].Instances[].InstanceId' --output text)
    # shellcheck disable=SC2086  # word splitting of the id list is intended
    [ -n "$IDS" ] && "${A[@]}" ec2 terminate-instances --instance-ids $IDS --output text | tee -a "$LOG"
  fi
  "${A[@]}" ec2 describe-instances --filters Name=tag:Project,Values=fleetkit --query 'Reservations[].Instances[].[InstanceId,State.Name]' --output text | tee -a "$LOG"
}
keep_note() { log "--keep: no teardown; the hosts end at their own shutdown timer. Tear down with host_count=0 support_count=0."; }
if [ -n "$KEEP" ]; then trap keep_note EXIT; else trap teardown EXIT; fi

sync_evidence() {
  local stage=$1
  "${A[@]}" s3 sync "s3://$BUCKET/runs/$RUN_ID" "$OUT/host" --quiet 2>>"$LOG" && log "evidence synced after $stage ($(find "$OUT/host" -type f | wc -l | tr -d ' ') files)"
}

# run <instance-id> <label> <timeout-seconds> <shell command on the host>; output saved to $OUT/<label>.txt
run() {
  local iid=$1 label=$2 to=$3 cmd=$4
  if ! session_ok; then log "AWS session expired before $label; stopping"; exit 2; fi
  if past_hard_stop; then log "past hard stop ($HARD_STOP_TEXT) before $label; stopping"; exit 3; fi
  local left=$(( HARD_STOP_EPOCH - $(date +%s) ))
  if [ "$to" -gt "$left" ]; then log "run: $label timeout cut from ${to}s to ${left}s by the hard stop"; to=$left; fi
  log "run: $label on $iid (timeout ${to}s)"
  local cid st
  cid=$("${A[@]}" ssm send-command --document-name AWS-RunShellScript --instance-ids "$iid" \
        --parameters "commands=[\"$cmd\"],executionTimeout=[\"$to\"]" \
        --output-s3-bucket-name "$BUCKET" --output-s3-key-prefix "runs/$RUN_ID/ssm/$label" \
        --query Command.CommandId --output text) || { log "send-command failed"; return 1; }
  for _ in $(seq 1 $(( to / 10 + 6 ))); do
    sleep 10
    st=$("${A[@]}" ssm get-command-invocation --command-id "$cid" --instance-id "$iid" --query Status --output text 2>/dev/null)
    case "$st" in Success|Failed|TimedOut|Cancelled) break;; esac
  done
  "${A[@]}" ssm get-command-invocation --command-id "$cid" --instance-id "$iid" \
      --query '[StandardOutputContent,StandardErrorContent]' --output text > "$OUT/$label.txt" 2>&1
  log "run: $label -> $st ($(wc -l < "$OUT/$label.txt" | tr -d ' ') lines)"
  sync_evidence "$label"
  [ "$st" = "Success" ]
}

# wait_ssm <instance-id>: up to 10 minutes for the SSM agent to register.
wait_ssm() {
  local s=''
  for _ in $(seq 1 40); do
    s=$("${A[@]}" ssm describe-instance-information --filters Key=InstanceIds,Values="$1" --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null)
    [ "$s" = "Online" ] && return 0; sleep 15
  done
  return 1
}

# first <terraform output name>: its first list element, empty if there is none.
first() { terraform output -json "$1" 2>/dev/null | python3 -c 'import json,sys; v=json.load(sys.stdin); print(v[0] if v else "")' 2>/dev/null; }

log "plan: $PLAN, commit $HEAD, hard stop $HARD_STOP_TEXT"
if [ "$PLAN" = capacity ]; then
  # The hosts check out HEAD from GitHub and read the config from it: fail here, not after the apply.
  [[ "$CAPACITY_CONFIG" =~ ^[A-Za-z0-9_-]+$ ]] || { log "CAPACITY_CONFIG must be a plain name"; trap - EXIT; exit 2; }
  [[ "$CAPACITY_TIMEOUT_S" =~ ^[0-9]+$ ]] || { log "CAPACITY_TIMEOUT_S must be whole seconds"; trap - EXIT; exit 2; }
  git -C "$REPO" cat-file -e "$HEAD:experiments/capacity/$CAPACITY_CONFIG.env" 2>/dev/null \
    || { log "experiments/capacity/$CAPACITY_CONFIG.env is not committed at HEAD"; trap - EXIT; exit 2; }
  [ -n "$(git -C "$REPO" branch -r --contains "$HEAD" 2>/dev/null)" ] \
    || { log "HEAD $HEAD is not on any remote branch; push it first"; trap - EXIT; exit 2; }
fi

cd "$STACK" || exit 1
if ! session_ok; then log "AWS session not valid; aborting before apply"; trap - EXIT; exit 2; fi
BUCKET=$(terraform output -raw results_bucket)

if [ -z "$SKIP_APPLY" ]; then
  log "apply: planning ${APPLY_VARS[*]}"
  terraform plan -input=false -no-color "${APPLY_VARS[@]}" -var repo_ref="$HEAD" -out=tfplan-up >"$OUT/apply-plan.txt" 2>&1
  if apply_ok "$OUT/apply-plan.txt"; then
    terraform apply -input=false -no-color tfplan-up >>"$OUT/apply-plan.txt" 2>&1 || { log "apply failed"; exit 1; }
    log "apply: created ${PLAN_ADDRS[*]}"
  else
    log "apply: UNEXPECTED PLAN (see apply-plan.txt); not applying"; trap - EXIT; exit 1
  fi
fi
IID=$(first host_instance_ids)
[ -n "$IID" ] || { log "no experiment host in the terraform outputs"; exit 1; }
log "host: $IID (self-terminates 4 h after boot)"
echo "$IID" > "$OUT/instance-id"
if [ "$PLAN" = capacity ]; then
  SID=$(first support_instance_ids)
  SUPPORT_IP=$(first support_private_ips)
  [ -n "$SID" ] && [ -n "$SUPPORT_IP" ] || { log "no support host in the terraform outputs"; exit 1; }
  log "support: $SID at $SUPPORT_IP (self-terminates 4 h after boot)"
  echo "$SID" > "$OUT/support-instance-id"
fi

log "waiting for SSM registration"
wait_ssm "$IID" || { log "host never registered with SSM"; exit 1; }
if [ "$PLAN" = capacity ]; then
  wait_ssm "$SID" || { log "support host never registered with SSM"; exit 1; }
fi

run "$IID" cloud-init 960 'timeout 900 cloud-init status --wait --long; echo EXIT=$?; cp /var/log/cloud-init-output.log /var/lib/fleetkit/ 2>/dev/null; true' || exit 1

if [ "$PLAN" = validation ]; then
  run "$IID" host-setup 1800 "cd /opt/fleetkit && git fetch -q && git checkout -q $HEAD && FLEETKIT_RUN_ID=$RUN_ID bash images/host/setup.sh 2>&1" || exit 1
  run "$IID" hostcheck 400 "FLEETKIT_RUN_ID=$RUN_ID bash /opt/fleetkit/images/host/hostcheck.sh" || exit 1
  run "$IID" trial-n1 1200 "FLEETKIT_RUN_ID=$RUN_ID bash /opt/fleetkit/images/host/stage.sh trial-n1 2>&1" || log "trial-n1 did not succeed; continuing to collect evidence"
  run "$IID" trial-n24 1800 "FLEETKIT_RUN_ID=$RUN_ID bash /opt/fleetkit/images/host/stage.sh trial-n24 2>&1" || log "trial-n24 did not succeed; continuing"
  run "$IID" smoke 2400 "FLEETKIT_RUN_ID=$RUN_ID bash /opt/fleetkit/images/host/stage.sh smoke 2>&1" || log "smoke did not succeed; continuing"
  run "$IID" bundle 900 "FLEETKIT_RUN_ID=$RUN_ID bash /opt/fleetkit/images/host/stage.sh bundle 2>&1" || log "bundle reported problems; evidence synced anyway"
else
  # The support host's cloud-init does all of its setup, so its done marker is required.
  run "$SID" support-cloud-init 960 'timeout 900 cloud-init status --wait --long; echo EXIT=$?; docker ps -a; test -f /var/lib/fleetkit/cloud-init-done' || exit 1
  ENVS="FIXTURE_URL=http://$SUPPORT_IP:8081 OTLP_ENDPOINT=http://$SUPPORT_IP:4318 METRICS_PERIOD=0.2"
  run "$IID" host-setup 1800 "cd /opt/fleetkit && git fetch -q && git checkout -q $HEAD && FLEETKIT_RUN_ID=$RUN_ID $ENVS bash images/host/setup.sh 2>&1" || exit 1
  run "$IID" hostcheck 400 "FLEETKIT_RUN_ID=$RUN_ID $ENVS bash /opt/fleetkit/images/host/hostcheck.sh" || exit 1
  run "$IID" capacity $(( CAPACITY_TIMEOUT_S + 600 )) "FLEETKIT_RUN_ID=$RUN_ID $ENVS CAPACITY_CONFIG=$CAPACITY_CONFIG CAPACITY_TIMEOUT_S=$CAPACITY_TIMEOUT_S bash /opt/fleetkit/images/host/stage.sh capacity 2>&1" || log "capacity did not succeed; continuing to collect evidence"
  run "$IID" bundle 900 "FLEETKIT_RUN_ID=$RUN_ID bash /opt/fleetkit/images/host/stage.sh bundle 2>&1" || log "bundle reported problems; evidence synced anyway"
  support_sync
fi
if [ -n "$KEEP" ]; then log "all stages attempted"; else log "all stages attempted; tearing down"; fi
exit 0
