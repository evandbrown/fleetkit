#!/bin/bash
# Launch a campaign: experiments/launch.sh experiments/campaigns/<name>.json [--dry-run] [--spent USD] [--runs RUN,...]
#   [--replace]  (with --runs: launch those runs again, archiving their earlier attempts under superseded/ first)
# What it checks and does: experiments/launcher/launch.py.
exec python3 "$(dirname "$0")/launcher/launch.py" "$@"
