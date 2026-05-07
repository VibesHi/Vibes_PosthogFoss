#!/usr/bin/env bash
#
# Shared helpers for the events-export container. Sourced from every
# other script under this directory.
#
# Mirrors docker/backup/scripts/_lib.sh in shape, intentionally — same
# logging conventions, same sentinel pattern, same `set -euo pipefail`
# applied at source time. Different cargo:
#
#   * SENTINEL_DIR: /var/run/events-export   (vs /var/run/backup)
#   * Auth env:    EVENTS_EXPORT_GCS_HMAC_*  (vs GCS_HMAC_*)
#   * Default bucket: gs://vibes-analytics-events/  (vs ${GCS_BUCKET})

set -euo pipefail

readonly SENTINEL_DIR=/var/run/events-export
mkdir -p "$SENTINEL_DIR"

# --- logging ----------------------------------------------------------------

log()  { printf '[%s] [%s] %s\n' "$(date -u +%FT%TZ)" "${SCRIPT_NAME:-events-export}" "$*"; }
warn() { log "WARN: $*"; }
die()  { log "ERROR: $*"; exit 1; }

ok() {
    local job="${1:-${SCRIPT_NAME}}"
    touch "${SENTINEL_DIR}/${job}.last-ok"
    log "OK -> ${SENTINEL_DIR}/${job}.last-ok"
}

# --- credentials ------------------------------------------------------------

require_gcs_creds() {
    : "${EVENTS_EXPORT_GCS_BUCKET:?EVENTS_EXPORT_GCS_BUCKET not set in env}"
    : "${EVENTS_EXPORT_GCS_HMAC_KEY:?EVENTS_EXPORT_GCS_HMAC_KEY not set in env}"
    : "${EVENTS_EXPORT_GCS_HMAC_SECRET:?EVENTS_EXPORT_GCS_HMAC_SECRET not set in env}"
}

require_ch_creds() {
    : "${CLICKHOUSE_PASSWORD:?CLICKHOUSE_PASSWORD not set in env}"
}

# --- bucket URL helpers -----------------------------------------------------
#
# EVENTS_EXPORT_GCS_BUCKET is stored with a `gs://` prefix in .env (matches
# the same convention as the backup container's GCS_BUCKET).
#
# fsspec's gcsfs does NOT support HMAC interop credentials, only OAuth /
# ADC / service-account-json. Inside the container we have HMAC, so we
# rewrite gs:// → s3:// and point s3fs at the GCS interop endpoint.
# `verify.sh` and the python script both consume the rewritten URL.

gcs_bucket_name() { echo "${EVENTS_EXPORT_GCS_BUCKET#gs://}"; }

s3_uri()  { echo "s3://$(gcs_bucket_name)/$1"; }                              # for aws-cli / s3fs
http_uri(){ echo "https://storage.googleapis.com/$(gcs_bucket_name)/$1"; }    # raw HTTP probe

# Output URL the python script consumes. Always s3:// + GCS interop endpoint.
events_output_url() {
    require_gcs_creds
    echo "s3://$(gcs_bucket_name)/"
}
