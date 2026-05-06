#!/usr/bin/env bash
#
# backup-ch-current-month.sh -- daily snapshot of the CURRENT month's
# sharded_events partition.
#
# `sharded_events` is partitioned by `toYYYYMM(timestamp)` (see
# posthog/models/event/sql.py:181). The current-month partition is the only
# one that's still being written to; old months are immutable and handled by
# backup-ch-freeze-last-month.sh.
#
# Restore semantics: each daily snapshot is self-contained. To restore the
# current month you only need the MOST RECENT current-month .zip. The other
# (up to 3) snapshots in clickhouse/events/current/ exist purely as fallback
# in case the most recent is corrupt -- bucket lifecycle deletes them at 3d.
#
# Compression level 3 (zstd) keeps daily run time tractable. Frozen-month
# script bumps to 9 because that runs once and is read rarely.
#
# Schedule: 30 3 * * * (daily 03:30 UTC).
# Sentinel: /var/run/backup/ch-current.last-ok.

SCRIPT_NAME=ch-current
# shellcheck source=_lib.sh
source "$(dirname "$0")/_lib.sh"

require_gcs_creds
require_ch_creds

PARTITION=$(date -u +%Y%m)
TS=$(date -u +%Y%m%dT%H%M%SZ)
KEY="clickhouse/events/current/${PARTITION}-${TS}.zip"
URL=$(http_uri "$KEY")

log "Backing up sharded_events partition ${PARTITION} -> ${URL}"

# CH BACKUP statement notes:
#   * PARTITION ID '...' filters parts to those matching the partition key.
#     For toYYYYMM the ID is the literal "YYYYMM" string.
#   * compression_method=zstd compression_level=3 gives ~4x compression at
#     ~200 MB/s on this hardware. lz4 would be 2x faster but ~30% larger.
#   * No `ASYNC` -- this script blocks on the HTTP call. CH server-side the
#     BACKUP runs synchronously inside the connection. For partitions that
#     would exceed the curl 30-min timeout, switch to `ASYNC` + ch_wait_backup_done.
ch_query "BACKUP TABLE posthog.sharded_events PARTITION ID '${PARTITION}'
          TO S3('${URL}', '${GCS_HMAC_KEY}', '${GCS_HMAC_SECRET}')
          SETTINGS compression_method = 'zstd', compression_level = 3" \
    >/dev/null \
    || die "BACKUP statement failed -- inspect 'docker compose logs clickhouse'"

# Defense in depth: even though the HTTP call returned 200, system.backups is
# the source of truth for the actual backup state. Catches the case where
# BACKUP returned the backup ID immediately but the parts upload failed
# asynchronously inside CH.
status=$(ch_query "SELECT status FROM system.backups WHERE name LIKE '%${PARTITION}-${TS}.zip%' ORDER BY start_time DESC LIMIT 1" | tr -d '[:space:]')
[ "$status" = "BACKUP_CREATED" ] \
    || die "BACKUP ended in status='${status}'. Inspect: SELECT * FROM system.backups WHERE name LIKE '%${TS}%' FORMAT Vertical"

log "Backup completed: status=${status}"
ok ch-current
