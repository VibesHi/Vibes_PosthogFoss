#!/usr/bin/env bash
#
# healthcheck.sh -- compose-level healthcheck for the backup container.
#
# Hooked up via `healthcheck:` in docker-compose.prod.yml. `docker compose ps`
# will show `(unhealthy)` if any backup job hasn't successfully run within
# its expected cadence (plus a grace window).
#
# Returns 0 if every job's last-ok sentinel is fresh enough.
# Returns 1 if any sentinel is missing OR older than its tolerance.
#
# Container-startup grace (start_period in compose) prevents this from going
# unhealthy in the first 36h before the first cron tick has fired.
#
# This is the cheapest of the three monitoring scripts:
#   * status.sh   -- pretty-print everything (human)
#   * healthcheck -- pass/fail via exit code (compose / monitoring)
#   * verify.sh   -- talk to GCS, validate sizes (slow, manual)

set -e

SENTINEL_DIR=/var/run/backup
NOW=$(date +%s)

declare -A MAX_AGE=(
    [pg]=$((26*3600))             # 26h  (1d cadence + 2h grace)
    [ch-current]=$((26*3600))     # 26h
    [ch-persons]=$((8*86400))     # 8d   (weekly + 1d grace)
    [ch-freeze]=$((33*86400))     # 33d  (monthly + 3d grace)
)

# Per-job grace window for a MISSING sentinel after container start. Critical
# for ch-freeze: cron only fires it on the 1st of each month, so on a fresh
# deploy it won't even attempt to run for up to ~30d. Generic 36h grace
# would mark the container unhealthy for ~28d on every fresh install.
#
# Rule: missing-grace = MAX_AGE[job] + 24h. Once the cron should have fired
# AND the result should be visible, missing == failed.
declare -A MISSING_GRACE=(
    [pg]=$((26*3600 + 86400))         # 50h
    [ch-current]=$((26*3600 + 86400)) # 50h
    [ch-persons]=$((8*86400 + 86400)) # 9d
    [ch-freeze]=$((33*86400))         # 33d -- one full cron interval
)

# Container start-time in seconds. /proc/1 mtime is a stable proxy for "when
# this container was created" (cron child processes don't bump it).
CONTAINER_BORN=$(stat -c %Y /proc/1 2>/dev/null || echo "$NOW")

failed=0
for job in "${!MAX_AGE[@]}"; do
    f="${SENTINEL_DIR}/${job}.last-ok"
    if [ ! -f "$f" ]; then
        # Within job-specific grace window after container start -> not yet a failure.
        # ch-freeze on a fresh install is the canonical case: it could be 30d
        # before the first run is even expected.
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
