#!/usr/bin/env bash
#
# Shared helpers for backup/restore scripts. Sourced from every other script
# under this directory.
#
# Conventions:
#   * SCRIPT_NAME: set by caller before sourcing (used in log prefix + sentinels).
#   * GCS auth: HMAC pair only (GCS_HMAC_KEY / GCS_HMAC_SECRET). Both `aws s3`
#     and ClickHouse `BACKUP TO S3()` consume the same pair.
#   * Sentinels: /var/run/backup/<job>.last-ok touched on success. healthcheck.sh
#     and status.sh consume these.
#   * `set -euo pipefail` is applied here, not per-script. Scripts that need
#     to disable a flag temporarily can `set +e` locally.

set -euo pipefail

readonly SENTINEL_DIR=/var/run/backup
mkdir -p "$SENTINEL_DIR"

# --- logging -----------------------------------------------------------------

log()  { printf '[%s] [%s] %s\n' "$(date -u +%FT%TZ)" "${SCRIPT_NAME:-backup}" "$*"; }
warn() { log "WARN: $*"; }
die()  { log "ERROR: $*"; exit 1; }

ok() {
    local job="${1:-${SCRIPT_NAME}}"
    touch "${SENTINEL_DIR}/${job}.last-ok"
    log "OK -> ${SENTINEL_DIR}/${job}.last-ok"
}

# --- credentials -------------------------------------------------------------

require_gcs_creds() {
    : "${GCS_BUCKET:?GCS_BUCKET not set in env}"
    : "${GCS_HMAC_KEY:?GCS_HMAC_KEY not set in env}"
    : "${GCS_HMAC_SECRET:?GCS_HMAC_SECRET not set in env}"
}

require_pg_creds() {
    : "${POSTHOG_DB_PASSWORD:?POSTHOG_DB_PASSWORD not set in env}"
}

require_ch_creds() {
    : "${CLICKHOUSE_PASSWORD:?CLICKHOUSE_PASSWORD not set in env}"
}

# --- GCS path helpers --------------------------------------------------------
#
# GCS_BUCKET is stored with a `gs://` prefix in .env (matches user convention).
# - For `aws s3` commands → keep `s3://<bucket-name>/...`.
# - For ClickHouse BACKUP TO S3() → use the HTTPS interop URL.
#
# Strip the prefix once here; never construct paths manually elsewhere.

gcs_bucket_name() { echo "${GCS_BUCKET#gs://}"; }

s3_uri()  { echo "s3://$(gcs_bucket_name)/$1"; }                              # for aws-cli
http_uri(){ echo "https://storage.googleapis.com/$(gcs_bucket_name)/$1"; }    # for CH BACKUP

# --- aws-cli wrapper ---------------------------------------------------------
#
# All aws-cli invocations in backup/restore scripts go through this wrapper so
# the GCS interop endpoint and HMAC creds are configured in exactly one place.
# `--no-progress` and `--quiet` suppress per-byte progress noise that floods
# `docker compose logs` for multi-GB uploads.

aws_gcs() {
    AWS_ACCESS_KEY_ID="${GCS_HMAC_KEY}" \
    AWS_SECRET_ACCESS_KEY="${GCS_HMAC_SECRET}" \
    AWS_DEFAULT_REGION="${GCS_REGION:-auto}" \
    aws --endpoint-url=https://storage.googleapis.com \
        --no-progress \
        s3 "$@"
}

# `aws s3 ls s3://bucket/missing_key` exits 0 with empty stdout when the
# key doesn't exist (only fails on auth / nonexistent bucket). For
# precondition checks in restore scripts we need a true exists-or-not
# probe. Check that the listing is non-empty.
gcs_object_exists() {
    local uri="$1"
    [ -n "$(aws_gcs ls "$uri" 2>/dev/null)" ]
}

# --- ClickHouse HTTP wrapper -------------------------------------------------
#
# `clickhouse-client` isn't packaged for alpine repos. Use the HTTP interface
# (port 8123) instead -- same auth, same SQL, just a different transport.
#
# `--fail-with-body` makes curl exit non-zero AND print the response body on
# 4xx/5xx, so the user sees CH error text in the log.

ch_query() {
    require_ch_creds
    local query="$1"
    curl -sS --fail-with-body \
        -H "X-ClickHouse-User: default" \
        -H "X-ClickHouse-Key: ${CLICKHOUSE_PASSWORD}" \
        "http://${CLICKHOUSE_HOST:-clickhouse}:${CLICKHOUSE_PORT_HTTP:-8123}/" \
        --data-urlencode "query=${query}"
}

# Polls system.backups for the most recent BACKUP/RESTORE command's status
# until it leaves the running state. Returns final status string on stdout
# (caller compares to "BACKUP_CREATED" / "RESTORED").

ch_wait_backup_done() {
    local timeout="${1:-3600}"
    local elapsed=0 status
    while [ "$elapsed" -lt "$timeout" ]; do
        status=$(ch_query "SELECT status FROM system.backups ORDER BY start_time DESC LIMIT 1" | tr -d '[:space:]')
        case "$status" in
            CREATING_BACKUP|RESTORING|"") sleep 5; elapsed=$((elapsed + 5));;
            *) echo "$status"; return 0;;
        esac
    done
    echo "TIMEOUT_AFTER_${timeout}s"
    return 1
}

# --- compose helpers (used by restore scripts) -------------------------------
#
# `docker compose` from inside the backup container would need the docker
# socket mounted, which is a security hole. Restore scripts that need to
# stop/start prod services do so from the HOST, not from inside the container.
# These helpers print the docker-compose command the operator should run --
# the script does NOT auto-execute them.

# NOTE: every service that holds an open connection to the `db` PG instance
# must be stopped, otherwise `pg_dumpall --clean` will drop databases out
# from under live consumers. This includes:
#   * `temporal` itself (uses POSTGRES_PWD against `db` for its own state)
#   * `cyclotron-janitor` (cdp profile, uses CYCLOTRON_DATABASE_URL → `db`)
# `docker compose stop` is no-op for services that aren't running, so it's
# safe to list profile-gated ones unconditionally.
print_stop_dependents_cmd() {
    cat <<'EOF'
docker compose stop \
    web worker temporal-django-worker temporal plugins \
    ingestion-general ingestion-sessionreplay ingestion-error-tracking \
    ingestion-logs ingestion-traces \
    recording-api hypercache-server \
    capture replay-capture property-defs-rs feature-flags cymbal \
    cyclotron-janitor
EOF
}

print_start_dependents_cmd() {
    cat <<'EOF'
docker compose up -d
EOF
}

# --- confirmation prompt -----------------------------------------------------

confirm_destructive() {
    local prompt="$1"
    if [ "${BACKUP_FORCE:-0}" = "1" ]; then
        warn "BACKUP_FORCE=1 set -- skipping confirmation prompt"
        return 0
    fi
    if [ ! -t 0 ]; then
        die "Refusing to proceed without a TTY. Re-run with -it OR set BACKUP_FORCE=1 (e.g. for unattended automation)."
    fi
    echo
    echo "================================================================"
    echo "$prompt"
    echo "================================================================"
    echo "Type YES (uppercase) to proceed:"
    read -r reply
    [ "$reply" = "YES" ] || die "Aborted by user"
}
