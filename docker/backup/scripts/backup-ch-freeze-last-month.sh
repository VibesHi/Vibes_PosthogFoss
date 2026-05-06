#!/usr/bin/env bash
#
# backup-ch-freeze-last-month.sh -- once-per-month, freeze the previous
# month's sharded_events partition forever.
#
# Once a month closes (e.g. on May 1st we freeze April), the partition is
# immutable. The frozen .zip in GCS is the sole long-term archive of that
# month -- bucket lifecycle has NO Delete rule for clickhouse/events/frozen/,
# only storage-class transitions (Coldline at 60d, Archive at 365d).
#
# Idempotency is critical here:
#   - Cron may double-fire if the host reboots within the cron minute.
#   - The script may be re-run manually after a partial failure.
#   - We MUST NOT overwrite an existing frozen artifact -- doing so would
#     replace the canonical record of that month with a possibly-different
#     dataset (rare but possible if backfills hit the partition between runs).
#
# Schedule: 0 4 1 * * (1st of every month, 04:00 UTC).
# Sentinel: /var/run/backup/ch-freeze.last-ok touched on EITHER success OR
#           skip-because-already-exists. Both are valid healthy states.

SCRIPT_NAME=ch-freeze
# shellcheck source=_lib.sh
source "$(dirname "$0")/_lib.sh"

require_gcs_creds
require_ch_creds

# Last month: e.g. on 2026-05-01 -> "202604".
#
# Avoid `date -d '1 month ago'` -- GNU date subtracts months naively, so on
# 2026-05-31 it returns 2026-05-01 (because 2026-04-31 is invalid and
# normalizes forward). Manual mid-month invocation would freeze the WRONG
# month.
#
# Instead: anchor to 1st-of-current-month, subtract 1 day. Always lands in
# the previous month regardless of today's day-of-month.
LAST_MONTH=$(date -u -d "$(date -u +%Y-%m-01) -1 day" +%Y%m)
KEY="clickhouse/events/frozen/${LAST_MONTH}.zip"
URL=$(http_uri "$KEY")

# Idempotency check: skip if the frozen artifact already exists.
# `aws s3 ls` exits 0 with a non-empty listing when the object is present,
# 1 (or empty output) otherwise.
if aws_gcs ls "$(s3_uri "$KEY")" 2>/dev/null | grep -q "${LAST_MONTH}.zip"; then
    log "${KEY} already frozen in GCS -- skipping (idempotent)"
    ok ch-freeze
    exit 0
fi

log "Freezing partition ${LAST_MONTH} -> ${URL}"

# compression_level=9 (max). This file:
#   - is uploaded ONCE in its lifetime
#   - will be tiered to Coldline after 60 days (read-rarely)
#   - represents an entire month's events
# So spending an extra few minutes of CPU on max compression saves money for
# years. Typical small install: 200MB raw -> ~50MB at level 9.
#
# Watch out: level 9 + large partition can push the curl call past 30min.
# If you hit that ceiling, switch to ASYNC + ch_wait_backup_done with a
# longer timeout.
ch_query "BACKUP TABLE posthog.sharded_events PARTITION ID '${LAST_MONTH}'
          TO S3('${URL}', '${GCS_HMAC_KEY}', '${GCS_HMAC_SECRET}')
          SETTINGS compression_method = 'zstd', compression_level = 9" \
    >/dev/null \
    || die "BACKUP statement failed -- inspect 'docker compose logs clickhouse'"

status=$(ch_query "SELECT status FROM system.backups WHERE name LIKE '%${LAST_MONTH}.zip%' ORDER BY start_time DESC LIMIT 1" | tr -d '[:space:]')
[ "$status" = "BACKUP_CREATED" ] \
    || die "Freeze BACKUP ended in status='${status}'"

# Final size for the log -- useful for capacity-planning the bucket budget.
size_bytes=$(aws_gcs ls --summarize "$(s3_uri "$KEY")" | awk '/Total Size:/ {print $3; exit}')
log "Frozen ${LAST_MONTH}: $(numfmt --to=iec --suffix=B "${size_bytes:-0}")"
ok ch-freeze
