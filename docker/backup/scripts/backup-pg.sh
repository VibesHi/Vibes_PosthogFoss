#!/usr/bin/env bash
#
# backup-pg.sh -- daily Postgres full-cluster dump to GCS.
#
# Streams `pg_dumpall --clean` through gzip directly to `aws s3 cp -`. No
# intermediate file on local disk -> no race against the backup container's
# limited tmpfs / volume size.
#
# `--clean` emits DROP statements so a restore is fully idempotent (drops
# existing databases before recreating). PostHog uses one Postgres database
# (`posthog`); pg_dumpall captures roles + the database in one stream.
#
# Schedule: 0 2 * * * (daily 02:00 UTC, see crontab).
# Retention: enforced by GCS bucket lifecycle (lifecycle.json), NOT here.
# Sentinel: /var/run/backup/pg.last-ok touched on success.

SCRIPT_NAME=pg
# shellcheck source=_lib.sh
source "$(dirname "$0")/_lib.sh"

require_gcs_creds
require_pg_creds

NOW=$(date -u +%Y%m%dT%H%M%SZ)
KEY="postgres/posthog-${NOW}.sql.gz"
DEST=$(s3_uri "$KEY")

log "Dumping Postgres -> ${DEST}"

# `pg_dumpall` exits non-zero on error AND prints to stderr; pipefail (set in
# _lib.sh) makes the whole pipeline fail in that case. `gzip -9` gets us ~10x
# compression on PG dumps; CPU is cheap on this run (once a day, ~30s total).
PGPASSWORD="${POSTHOG_DB_PASSWORD}" \
    pg_dumpall \
        --clean \
        --if-exists \
        --no-password \
        -h "${PGHOST:-db}" \
        -U "${PGUSER:-posthog}" \
    | gzip -9 \
    | aws_gcs cp - "${DEST}"

# Verify the upload landed and is at least 1 KB. Catches the silent-failure
# mode where pg_dumpall produces no output (e.g. role permission issue) but
# `gzip` and `aws s3` still succeed on an empty stream.
size_bytes=$(aws_gcs ls --summarize "${DEST}" | awk '/^Total Size:/ {print $3; exit}')
if [ "${size_bytes:-0}" -lt 1024 ]; then
    die "Uploaded dump is suspiciously small (${size_bytes:-0} bytes). Inspect with: aws --endpoint-url=https://storage.googleapis.com s3 ls ${DEST}"
fi

log "Dump uploaded ($(numfmt --to=iec --suffix=B "${size_bytes}"))"
ok pg
