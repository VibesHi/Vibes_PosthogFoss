#!/usr/bin/env bash
#
# healthcheck.sh -- compose-level healthcheck for the events-export
# container.
#
# Hooked up via `healthcheck:` in docker-compose.prod.yml. `docker compose
# ps` will show `(unhealthy)` if the events-export job hasn't successfully
# run within its expected cadence (plus a grace window).
#
# Returns 0 if the events-export sentinel is fresh enough.
# Returns 1 if the sentinel is missing OR older than its tolerance.
#
# Container-startup grace (start_period in compose + MISSING_GRACE here)
# prevents this from going unhealthy in the first ~30h before the first
# cron tick has fired.
#
# This is the cheapest of the three monitoring scripts:
#   * status.sh   -- pretty-print sentinel state (human)
#   * healthcheck -- pass/fail via exit code (compose / monitoring)
#   * verify.sh   -- talk to GCS, validate sizes (slow, manual)

set -e

SENTINEL_DIR=/var/run/events-export
NOW=$(date +%s)

# 26h = 24h cron + 2h grace.
declare -A MAX_AGE=(
    [events-export]=$((26*3600))
)

# Per-job grace window for a MISSING sentinel after container start.
# Generic 26h + 24h = 50h: gives one full cron cycle PLUS a day of slack
# for first-deploy edge cases (bucket auth being fixed up after first
# boot, time-zone oddities, etc.).
declare -A MISSING_GRACE=(
    [events-export]=$((26*3600 + 86400))
)

# /proc/1 mtime is a stable proxy for "when this container was created"
# (cron child processes don't bump it).
CONTAINER_BORN=$(stat -c %Y /proc/1 2>/dev/null || echo "$NOW")

failed=0
for job in "${!MAX_AGE[@]}"; do
    f="${SENTINEL_DIR}/${job}.last-ok"
    if [ ! -f "$f" ]; then
        if [ "$((NOW - CONTAINER_BORN))" -lt "${MISSING_GRACE[$job]}" ]; then
            continue
        fi
        echo "MISSING: ${job} (sentinel never created; container is $((  (NOW - CONTAINER_BORN) / 3600 ))h old, grace=${MISSING_GRACE[$job]}s)"
        failed=1
        continue
    fi
    age=$((NOW - $(stat -c %Y "$f")))
    if [ "$age" -gt "${MAX_AGE[$job]}" ]; then
        echo "STALE: ${job} (age=${age}s, max=${MAX_AGE[$job]}s)"
        failed=1
    fi
done

exit "$failed"
