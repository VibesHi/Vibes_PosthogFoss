#!/usr/bin/env bash
#
# export-events.sh -- cron entrypoint for the events export container.
#
# Wraps the Python script with:
#   * GCS HMAC creds → AWS_* env vars consumable by s3fs
#   * gs:// → s3:// + GCS interop endpoint rewrite (fsspec/gcsfs doesn't
#     speak HMAC; s3fs does, so we go through the interop endpoint)
#   * Sentinel touch on success
#   * `LOG_LEVEL` passthrough so `docker compose exec posthog-events-export
#     /usr/local/bin/events-export-scripts/export-events.sh` is debuggable
#
# Call shape:
#   export-events.sh                       # default: --yesterday, all teams
#   export-events.sh --date 2024-03-28
#   export-events.sh --range 2024-01-01 2024-03-31 --team-ids 1,2
#   export-events.sh --yesterday --overwrite
#
# Whatever args you pass are forwarded to export_daily.py verbatim, EXCEPT
# we provide a default `--yesterday` if no day-selecting flag is given.

SCRIPT_NAME=events-export
# shellcheck source=_lib.sh
source "$(dirname "$0")/_lib.sh"

require_gcs_creds
require_ch_creds

# fsspec/s3fs reads creds from standard AWS env vars. Pointing
# AWS_ENDPOINT_URL at the GCS interop endpoint makes s3fs talk to GCS
# transparently — `s3://my-bucket/...` is then a real GCS object.
export AWS_ACCESS_KEY_ID="${EVENTS_EXPORT_GCS_HMAC_KEY}"
export AWS_SECRET_ACCESS_KEY="${EVENTS_EXPORT_GCS_HMAC_SECRET}"
export AWS_DEFAULT_REGION="${EVENTS_EXPORT_GCS_REGION:-auto}"
export AWS_ENDPOINT_URL="https://storage.googleapis.com"

# CH connection (passed to python via env, NOT CLI flags, so secrets
# don't show up in `docker compose top` output).
export CLICKHOUSE_HOST="${CLICKHOUSE_HOST:-clickhouse}"
export CLICKHOUSE_PORT_HTTP="${CLICKHOUSE_PORT_HTTP:-8123}"
export CLICKHOUSE_USER="${CLICKHOUSE_USER:-default}"
export CLICKHOUSE_DATABASE="${CLICKHOUSE_DATABASE:-posthog}"

OUTPUT="$(events_output_url)"

# If caller didn't pick a day-mode flag, default to yesterday. Cron
# invocations almost always want this.
HAS_DAY_FLAG=0
for arg in "$@"; do
    case "$arg" in
        --yesterday|--date|--range) HAS_DAY_FLAG=1 ;;
    esac
done

ARGS=("$@")
if [ "$HAS_DAY_FLAG" -eq 0 ]; then
    ARGS=(--yesterday "${ARGS[@]}")
fi

log "starting export. output=${OUTPUT} args=${ARGS[*]}"

cd "${POSTHOG_EVENTS_EXPORT_DIR:-/opt/posthog_events_export}"

python export_daily.py \
    --output "${OUTPUT}" \
    --log-level "${LOG_LEVEL:-INFO}" \
    "${ARGS[@]}"

ok events-export
