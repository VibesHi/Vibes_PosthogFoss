#!/usr/bin/env bash
#
# restore-pg.sh -- in-place destructive restore of the Postgres cluster from
# a GCS dump.
#
# DESTRUCTIVE. Drops and recreates the `posthog` database in the live PG
# instance. Caller MUST stop dependent services before running this --
# Django/Node/Rust services hold open connections and will see partial state
# (TableNotExistsError, then crash-loop) during the restore.
#
# Usage (from inside the backup container):
#   restore-pg.sh                       # use the most recent backup in GCS
#   restore-pg.sh latest                # same
#   restore-pg.sh s3://bucket/postgres/posthog-20260506T020000Z.sql.gz
#   restore-pg.sh postgres/posthog-20260506T020000Z.sql.gz
#
# Or from the host:
#   docker compose exec -it backup /usr/local/bin/backup-scripts/restore-pg.sh
#
# Safety net: before importing, dumps the CURRENT live PG state to
# /var/run/backup/pre-restore-pg-<TS>.sql.gz inside the container's volume.
# If the restore is wrong, you can revert with `gunzip -c ... | psql`.
#
# This script does NOT touch other services. It will REFUSE to run if it
# detects active web/worker connections (those services need to be stopped
# first to prevent crash-loops during the restore).

SCRIPT_NAME=restore-pg
# shellcheck source=_lib.sh
source "$(dirname "$0")/_lib.sh"

require_gcs_creds
require_pg_creds

ARG="${1:-latest}"

# --- resolve the source key --------------------------------------------------
if [ "$ARG" = "latest" ]; then
    log "Listing postgres/ in GCS to find the most recent dump..."
    KEY=$(aws_gcs ls "$(s3_uri "postgres/")" \
        | awk '/posthog-.*\.sql\.gz$/ {print $4}' \
        | sort \
        | tail -n 1)
    [ -n "$KEY" ] || die "No posthog-*.sql.gz found in s3://$(gcs_bucket_name)/postgres/"
    SRC=$(s3_uri "postgres/${KEY}")
elif [[ "$ARG" == s3://* ]]; then
    SRC="$ARG"
elif [[ "$ARG" == postgres/* ]]; then
    SRC=$(s3_uri "$ARG")
else
    die "Unrecognized argument: '${ARG}'. Pass 'latest', a postgres/...sql.gz key, or a full s3:// URI."
fi

log "Source: ${SRC}"

# --- verify source exists BEFORE doing anything destructive ------------------
# Fail-fast: a typo'd argument or stale lifecycle deletion shouldn't waste
# 30s and 200MB-1GB of disk on a pre-restore snapshot we can't actually use.
gcs_object_exists "${SRC}" \
    || die "Source key not found in GCS: ${SRC}. List candidates with: docker compose exec backup bash -lc 'source /usr/local/bin/backup-scripts/_lib.sh && aws_gcs ls $(s3_uri postgres/)'"

# --- pre-flight: detect live connections -------------------------------------
# 'posthog'-as-name backends are dependent services. Catch the most common
# operator mistake (forgetting to stop them) before we destroy data.
#
# `psql` failure here means we can't even connect -- if so, restore would
# fail too. Die before doing anything destructive (no snapshot, no DROP).
log "Checking for live PG connections..."
if ! ACTIVE=$(PGPASSWORD="${POSTHOG_DB_PASSWORD}" \
    psql -h "${PGHOST:-db}" -U "${PGUSER:-posthog}" -d posthog -tAc \
        "SELECT count(*) FROM pg_stat_activity
         WHERE datname='posthog' AND pid <> pg_backend_pid()
           AND application_name NOT LIKE 'pg_%'"); then
    die "Failed to connect to PG at ${PGHOST:-db} as ${PGUSER:-posthog}. Refusing to restore -- if we can't connect to query state, we can't restore either. Verify POSTHOG_DB_PASSWORD and that the db service is up."
fi
ACTIVE=${ACTIVE//[[:space:]]/}
if [ "${ACTIVE:-0}" -gt 0 ]; then
    warn "${ACTIVE} active backend(s) on posthog DB. Restoring with live consumers will produce TableNotExistsError storms."
    warn "Stop dependents from the HOST FIRST, then re-run this script:"
    print_stop_dependents_cmd
    confirm_destructive "Continue ANYWAY (will crash-loop ${ACTIVE} consumers)?"
fi

# --- pre-restore safety net --------------------------------------------------
PRE_RESTORE="${SENTINEL_DIR}/pre-restore-pg-$(date -u +%Y%m%dT%H%M%SZ).sql.gz"
log "Snapshotting current PG state -> ${PRE_RESTORE} (revert with: gunzip -c ${PRE_RESTORE} | psql -h db -U posthog)"
PGPASSWORD="${POSTHOG_DB_PASSWORD}" \
    pg_dumpall --clean --if-exists --no-password \
        -h "${PGHOST:-db}" -U "${PGUSER:-posthog}" \
    | gzip -9 > "${PRE_RESTORE}"
log "Pre-restore snapshot: $(stat -c %s "${PRE_RESTORE}" | numfmt --to=iec --suffix=B)"

# --- confirmation prompt -----------------------------------------------------
confirm_destructive "About to DROP and RECREATE the posthog database from:
  ${SRC}
Pre-restore snapshot saved at: ${PRE_RESTORE}
This is IRREVERSIBLE except via the pre-restore snapshot."

# --- perform restore ---------------------------------------------------------
# `psql -v ON_ERROR_STOP=1` aborts on the first SQL error instead of plowing
# through and leaving a half-restored DB. Critical because pg_dumpall's
# DROP DATABASE / CREATE DATABASE statements can't be wrapped in one txn.
#
# The sed filter strips role-management for the connecting user. pg_dumpall
# --clean emits `DROP ROLE IF EXISTS <user>; CREATE ROLE <user>; ALTER ROLE
# <user> WITH ...` which Postgres refuses with `current user cannot be
# dropped` when we're connected as that user (chicken-and-egg). The role
# already exists with the right grants (we just connected with it), so
# skipping these lines is safe and keeps the rest of the dump intact.
RESTORE_USER="${PGUSER:-posthog}"
log "Streaming restore from ${SRC} (filtering DROP/CREATE/ALTER ROLE ${RESTORE_USER})..."
aws_gcs cp "${SRC}" - \
    | gunzip -c \
    | sed -E "/^DROP ROLE IF EXISTS ${RESTORE_USER};\$/d; /^CREATE ROLE ${RESTORE_USER};\$/d; /^ALTER ROLE ${RESTORE_USER} WITH /d" \
    | PGPASSWORD="${POSTHOG_DB_PASSWORD}" \
        psql --set ON_ERROR_STOP=1 \
             -h "${PGHOST:-db}" -U "${PGUSER:-posthog}" \
             -d postgres \
             -q

log "Restore complete."
log "Next steps (run from the HOST):"
print_start_dependents_cmd
ok restore-pg
