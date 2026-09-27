#!/bin/bash
# Support host evidence at the end of a capacity run, over SSM Run Command as root:
#   FLEETKIT_RUN_ID=<run-id> bash images/support/sync.sh
# Stops the observability container with a 60 s grace, which lets the collector
# flush its file exporters, then uploads the OTLP-JSON files, the 1 Hz support
# metrics and cloud-init's log to s3://<results bucket>/runs/<run-id>/support/.
# The fixture keeps running. Exits non-zero if the stop or an upload failed.
set -uo pipefail
FK=/var/lib/fleetkit
RUN_ID=${FLEETKIT_RUN_ID:?FLEETKIT_RUN_ID}
BUCKET=$(cat "$FK/results-bucket")
DEST="s3://$BUCKET/runs/$RUN_ID/support"
t() { printf '%s %s\n' "$(date '+%H:%M:%S')" "$*"; }
rc=0

t "stopping fleetkit-lgtm (60 s grace)"
docker stop -t 60 fleetkit-lgtm >/dev/null || { t "docker stop failed"; rc=1; }
docker ps -a --filter label=fleetkit.role --format '{{.Names}} {{.Status}} {{.Image}}'
ls -la "$FK/lgtm/otlp"
wc -l "$FK/support-metrics.csv"

t "upload to runs/$RUN_ID/support/"
aws s3 sync --quiet "$FK/lgtm/otlp" "$DEST/otlp/" || { t "otlp upload failed"; rc=1; }
aws s3 cp --quiet "$FK/support-metrics.csv" "$DEST/support-metrics.csv" || { t "support-metrics upload failed"; rc=1; }
aws s3 cp --quiet /var/log/cloud-init-output.log "$DEST/cloud-init-output.log" || t "cloud-init log not uploaded"
t "support sync done (rc $rc)"
exit $rc
