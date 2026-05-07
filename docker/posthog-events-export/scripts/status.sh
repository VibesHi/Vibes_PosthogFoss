#!/usr/bin/env bash
#
# status.sh -- pretty-print the local sentinel state of the events
# export job.
#
# Reads /var/run/events-export/<job>.last-ok timestamps. Answers "did the
# cron RUN successfully?" -- NOT "are the artifacts in GCS valid?". For
# the latter see verify.sh.
#
# Output format:
#   JOB             LAST OK (UTC)          AGE          STATUS
#   ---             -------------          ---          ------
#   events-export   2026-05-07T06:00:14Z   17h41m       OK
#
# Usage from host:
#   docker compose exec posthog-events-export \\
#       /usr/local/bin/events-export-scripts/status.sh

# Don't `set -e` here -- we want to print all rows even if some are missing.
set -uo pipefail

SENTINEL_DIR=/var/run/events-export
NOW=$(date +%s)

JOBS=(events-export)

# Per-job expected cadence (seconds). 26h = 24h cron + 2h grace.
declare -A MAX_AGE=(
    [events-export]=$((26*3600))
)

printf '%-22s %-22s %-12s %s\n' "JOB" "LAST OK (UTC)" "AGE" "STATUS"
printf '%-22s %-22s %-12s %s\n' "---" "-------------" "---" "------"

for job in "${JOBS[@]}"; do
    f="${SENTINEL_DIR}/${job}.last-ok"
    if [ ! -f "$f" ]; then
        printf '%-22s %-22s %-12s %s\n' "$job" "—" "—" "NEVER"
        continue
    fi
    ts=$(stat -c %Y "$f")
    age=$((NOW - ts))
    age_d=$((age / 86400))
    age_h=$((age % 86400 / 3600))
    age_m=$((age % 3600 / 60))
    if [ "$age_d" -gt 0 ]; then
        age_str="${age_d}d ${age_h}h"
    else
        age_str="${age_h}h${age_m}m"
    fi
    when=$(date -u -d "@${ts}" +%FT%TZ)

    if [ -n "${MAX_AGE[$job]:-}" ] && [ "$age" -gt "${MAX_AGE[$job]}" ]; then
        status="STALE"
    else
        status="OK"
    fi
    printf '%-22s %-22s %-12s %s\n' "$job" "$when" "$age_str" "$status"
done
