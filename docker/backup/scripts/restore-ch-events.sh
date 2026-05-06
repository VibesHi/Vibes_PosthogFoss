#!/usr/bin/env bash
#
# restore-ch-events.sh -- in-place destructive restore of a single
# sharded_events partition from GCS.
#
# DESTRUCTIVE: drops the target partition from the live posthog.sharded_events
# before restoring. Other partitions are untouched.
#
# Usage:
#   restore-ch-events.sh <YYYYMM>                  # auto-pick frozen vs current
#   restore-ch-events.sh <YYYYMM> frozen           # force frozen artifact
#   restore-ch-events.sh <YYYYMM> current          # force most-recent current snap
#   restore-ch-events.sh <YYYYMM> <full-s3-key>    # explicit object key
#
# Examples:
#   restore-ch-events.sh 202604                    # restore April 2026 (frozen)
#   restore-ch-events.sh 202605                    # restore May 2026 (current)
#   restore-ch-events.sh 202605 current
#   restore-ch-events.sh 202604 clickhouse/events/frozen/202604.zip
#
# Auto-pick logic: if YYYYMM is the current month, look in current/. Otherwise
# look in frozen/. Override by passing 'frozen' or 'current' explicitly.
#
# Safety net: before DROP PARTITION, calls `ALTER TABLE ... FREEZE PARTITION`
# which creates a hardlinked snapshot inside the CH data directory at
# /var/lib/clickhouse/shadow/. Cheap (no copy, just inodes) and gives you a
# fully recoverable in-place rollback if the restore is wrong. Clean up
# manually with `rm -rf /var/lib/clickhouse/shadow/<freeze-name>/` from
# inside the clickhouse container once you trust the restore.
#
# Why not `SYSTEM UNFREEZE`? Recent CH builds may disable it in stock
# configs (it can race with replication). Plain `rm -rf` against the
# shadow path is always safe -- shadow/ is operator-managed, CH never
# touches it autonomously.

SCRIPT_NAME=restore-ch-events
# shellcheck source=_lib.sh
source "$(dirname "$0")/_lib.sh"

require_gcs_creds
require_ch_creds

PARTITION="${1:-}"
SOURCE_HINT="${2:-auto}"

[ -n "$PARTITION" ] || die "Usage: $0 <YYYYMM> [frozen|current|<s3-key>]"
[[ "$PARTITION" =~ ^[0-9]{6}$ ]] || die "Invalid PARTITION '${PARTITION}'. Expected YYYYMM (e.g. 202604)."

CURRENT_MONTH=$(date -u +%Y%m)

# --- resolve the source key --------------------------------------------------
case "$SOURCE_HINT" in
    auto)
        if [ "$PARTITION" = "$CURRENT_MONTH" ]; then
            SOURCE_HINT=current
        else
            SOURCE_HINT=frozen
        fi
        log "Auto-picked source class: ${SOURCE_HINT}"
        ;;
esac

case "$SOURCE_HINT" in
    frozen)
        KEY="clickhouse/events/frozen/${PARTITION}.zip"
        SRC_HTTP=$(http_uri "$KEY")
        ;;
    current)
        log "Listing clickhouse/events/current/ for partition ${PARTITION}..."
        FOUND=$(aws_gcs ls "$(s3_uri "clickhouse/events/current/")" \
            | awk -v pfx="${PARTITION}-" '$4 ~ pfx {print $4}' \
            | sort \
            | tail -n 1)
        [ -n "$FOUND" ] || die "No current/${PARTITION}-*.zip found in GCS. Try frozen?"
        KEY="clickhouse/events/current/${FOUND}"
        SRC_HTTP=$(http_uri "$KEY")
        ;;
    clickhouse/*)
        KEY="$SOURCE_HINT"
        SRC_HTTP=$(http_uri "$KEY")
        ;;
    *)
        die "Bad source hint '${SOURCE_HINT}'. Use frozen, current, or a clickhouse/... key."
        ;;
esac

log "Restoring partition ${PARTITION} from ${SRC_HTTP}"

# --- verify the source exists in GCS before destroying anything --------------
gcs_object_exists "$(s3_uri "$KEY")" \
    || die "Source key not found in GCS: $(s3_uri "$KEY")"

# --- pre-restore freeze (in-place rollback path) -----------------------------
# Use underscores, not hyphens: CH 22.3+ URL-encodes special chars in the
# on-disk shadow path, so `WITH NAME 'pre-restore-...'` becomes a directory
# named `pre%2Drestore%2D...` -- which makes the cleanup `rm -rf` we print
# below not match the actual path. Underscores survive the encoding intact.
FREEZE_NAME="pre_restore_${PARTITION}_$(date -u +%Y%m%dT%H%M%SZ)"
log "Freezing current partition ${PARTITION} as '${FREEZE_NAME}' (rollback hardlinks under /var/lib/clickhouse/shadow/)"
ch_query "ALTER TABLE posthog.sharded_events FREEZE PARTITION ID '${PARTITION}' WITH NAME '${FREEZE_NAME}'" \
    >/dev/null \
    || warn "FREEZE failed (partition may not exist yet -- continuing). To rollback later you'll need a different mechanism."

# --- confirmation prompt -----------------------------------------------------
confirm_destructive "About to DROP partition ${PARTITION} from posthog.sharded_events
and RESTORE it from ${SRC_HTTP}.
Pre-restore freeze: shadow/${FREEZE_NAME}/ (rollback via SYSTEM RESTORE REPLICA or manual ATTACH PART).
This is IN-PLACE on the LIVE ClickHouse instance -- queries against this partition will ERROR until restore completes."

# --- destroy + restore -------------------------------------------------------
log "Dropping partition ${PARTITION}..."
ch_query "ALTER TABLE posthog.sharded_events DROP PARTITION ID '${PARTITION}'" >/dev/null \
    || die "DROP PARTITION failed"

log "Restoring..."
ch_query "RESTORE TABLE posthog.sharded_events PARTITION ID '${PARTITION}'
          FROM S3('${SRC_HTTP}', '${GCS_HMAC_KEY}', '${GCS_HMAC_SECRET}')
          SETTINGS allow_non_empty_tables = true" \
    >/dev/null \
    || die "RESTORE statement failed -- partition is now EMPTY. Recover from FREEZE shadow at /var/lib/clickhouse/shadow/${FREEZE_NAME}/ or re-run with a different source."

# Verify final state
status=$(ch_query "SELECT status FROM system.backups ORDER BY start_time DESC LIMIT 1" | tr -d '[:space:]')
[ "$status" = "RESTORED" ] || die "RESTORE ended in status='${status}'"

ROW_COUNT=$(ch_query "SELECT count() FROM posthog.sharded_events WHERE toYYYYMM(timestamp) = ${PARTITION}" | tr -d '[:space:]')
log "Partition ${PARTITION} restored: ${ROW_COUNT} rows"

log "Rollback freeze kept at /var/lib/clickhouse/shadow/${FREEZE_NAME}/ in the clickhouse container."
log "Once you trust the restore, clean it up MANUALLY from the HOST:"
log "  docker compose exec clickhouse rm -rf /var/lib/clickhouse/shadow/${FREEZE_NAME}/"
log "(cleanup-restore-rollbacks.sh does NOT touch CH shadows -- backup container can't see CH's data volume.)"

ok restore-ch-events
