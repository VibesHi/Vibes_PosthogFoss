#!/usr/bin/env bash
#
# cleanup-restore-rollbacks.sh -- ages out local rollback artifacts created
# by restore-pg.sh.
#
# Each `restore-pg.sh` run snapshots the pre-restore state to
# /var/run/backup/pre-restore-pg-<TS>.sql.gz BEFORE importing. Useful for
# ~24-72h after a restore (rollback window). After that, the snapshots
# accumulate and chew up disk on the named volume (`posthog_backup_state`).
# Cron this nightly to clear the backlog.
#
# CH freezes (created by restore-ch-events.sh) live under
# /var/lib/clickhouse/shadow/pre-restore-*/ INSIDE the clickhouse
# container. The backup container can't see that path -- mounting CH's
# data volume here would give us write access to live parts and merges,
# which is unsafe. Operators clean CH freezes manually via the rm -rf
# command printed by restore-ch-events.sh on success. (PR for a separate
# tiny shadow-cleanup volume mount welcome if this becomes a real burden.)
#
# Usage:
#   cleanup-restore-rollbacks.sh                      # default: 7d retention
#   cleanup-restore-rollbacks.sh 14                   # custom retention (days)
#   cleanup-restore-rollbacks.sh --dry-run            # show what would be deleted
#   cleanup-restore-rollbacks.sh 14 --dry-run         # both
#
# `find -mtime +N` semantics: matches files modified MORE than N*24h ago,
# i.e. >N days, NOT >=N. So `+7` deletes files at least 8 days old. We
# trade the off-by-one for the standard interpretation; if you really want
# "older than 7d" pass `6`.
#
# Idempotent.

SCRIPT_NAME=cleanup-restore-rollbacks
# shellcheck source=_lib.sh
source "$(dirname "$0")/_lib.sh"

DAYS=7
DRY_RUN=0

for arg in "$@"; do
    if [ "$arg" = "--dry-run" ]; then
        DRY_RUN=1
    elif [[ "$arg" =~ ^[0-9]+$ ]]; then
        DAYS="$arg"
    else
        die "Unrecognized argument: '${arg}'. Usage: $0 [days] [--dry-run]"
    fi
done

# `find` returns nothing when no files match -- no shell-glob expansion
# involved, so no nullglob needed. `mapfile` is the safe way to read
# newline-separated paths into a bash array (handles spaces, won't
# word-split on IFS surprises).
mapfile -t matches < <(find "${SENTINEL_DIR}" -maxdepth 1 -type f -name 'pre-restore-pg-*.sql.gz' -mtime +"$DAYS" 2>/dev/null)

if [ "${#matches[@]}" -eq 0 ]; then
    log "No PG pre-restore snapshots older than ${DAYS}d. Nothing to do."
    ok cleanup-restore-rollbacks
    exit 0
fi

log "Found ${#matches[@]} PG pre-restore snapshot(s) older than ${DAYS}d:"
total_bytes=0
for f in "${matches[@]}"; do
    bytes=$(stat -c %s "$f" 2>/dev/null || echo 0)
    total_bytes=$((total_bytes + bytes))
    log "  $(basename "$f") ($(numfmt --to=iec --suffix=B "$bytes"))"
done
log "Total reclaimable: $(numfmt --to=iec --suffix=B "$total_bytes")"

if [ "$DRY_RUN" = "1" ]; then
    log "DRY RUN -- nothing deleted. Re-run without --dry-run to delete."
    exit 0
fi

for f in "${matches[@]}"; do
    rm -f "$f" || warn "Failed to delete $f"
done
log "Deleted ${#matches[@]} snapshot(s)."
ok cleanup-restore-rollbacks
