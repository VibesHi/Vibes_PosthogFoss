#!/usr/bin/env bash
#
# status.sh -- pretty-print the local sentinel state of every backup job.
#
# Reads /var/run/backup/<job>.last-ok timestamps. This answers "did the cron
# RUN successfully?" -- NOT "are the artifacts in GCS valid?". For the latter
# see verify.sh.
#
# Output format:
#   JOB             LAST OK (UTC)          AGE          STATUS
#   ---             -------------          ---          ------
#   pg              2026-05-06T02:00:14Z   17h41m       OK
#   ch-current      2026-05-06T03:33:02Z   16h08m       OK
#   ch-persons      —                      —            NEVER
#   ch-freeze       2026-05-01T04:00:51Z   5d 15h       OK
#
# Usage from host:
#   docker compose exec backup /usr/local/bin/backup-scripts/status.sh

# Don't `set -e` here -- we want to print all rows even if some are missing.
set -uo pipefail

SENTINEL_DIR=/var/run/backup
NOW=$(date +%s)

JOBS=(pg ch-current ch-persons ch-freeze restore-pg restore-ch-events restore-ch-persons)

# Per-job expected cadence (seconds). Used to colorize the STATUS column when
# a sentinel is older than its expected interval. Restore jobs have no
# cadence -- they're event-driven, so STALE doesn't apply.
declare -A MAX_AGE=(
    [pg]=$((26*3600))
    [ch-current]=$((26*3600))
    [ch-persons]=$((8*86400))
    [ch-freeze]=$((33*86400))
)

# Header
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
