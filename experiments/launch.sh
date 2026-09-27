#!/bin/bash
# Launch a campaign: experiments/launch.sh experiments/campaigns/<name>.json [--dry-run] [--spent USD] [--runs RUN,...]
# What it checks and does: experiments/launcher/launch.py.
exec python3 "$(dirname "$0")/launcher/launch.py" "$@"
