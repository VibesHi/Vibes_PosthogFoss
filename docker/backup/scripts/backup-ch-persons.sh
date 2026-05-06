#!/usr/bin/env bash
#
# backup-ch-persons.sh -- weekly full-database backup of ClickHouse,
# excluding sharded_events and disposable system_log tables.
#
# What's actually in here:
#   * person, person_distinct_id2, person_distinct_id_overrides, person_overrides
#   * groups, cohortpeople
#   * sharded_session_replay_events (metadata only, blobs in SeaweedFS)
#   * dashboards, insights, definitions, plugins... (NOT in CH; in PG)
#   * sessions, raw_sessions_v2/v3, channel_type, exchange_rate
#   * + every other table that isn't in the EXCEPT list below
#
# What's deliberately EXCLUDED:
#   * sharded_events                  -> handled by current/freeze scripts
#   * sharded_session_replay_events   -> see note (excluded; metadata is
#                                        useless without SeaweedFS blobs which
#                                        we sync separately via rclone)
#   * sharded_app_metrics(_2)         -> derivable telemetry
#   * sharded_log_entries             -> TTL'd, disposable
#   * sharded_query_log_archive       -> TTL'd, disposable
#   * sharded_ingestion_warnings      -> TTL'd, disposable
#   * writable_events, events_recent  -> materialized views (rebuilt on schema apply)
#
# Schedule: 30 4 * * 0 (Sunday 04:30 UTC, ~10min on a busy install).
# Sentinel: /var/run/backup/ch-persons.last-ok.

SCRIPT_NAME=ch-persons
# shellcheck source=_lib.sh
source "$(dirname "$0")/_lib.sh"

require_gcs_creds
require_ch_creds

TS=$(date -u +%Y%m%dT%H%M%SZ)
KEY="clickhouse/persons-weekly/${TS}.zip"
URL=$(http_uri "$KEY")

log "Backing up posthog DB (excl. events / system_log / aggregates) -> ${URL}"

# CH BACKUP DATABASE ... EXCEPT TABLES syntax:
# https://clickhouse.com/docs/en/operations/backup#configure-a-backup-destination
#
# All EXCEPT tables must be unqualified table names (no `posthog.` prefix)
# because EXCEPT is scoped to the named database already.
ch_query "BACKUP DATABASE posthog
          EXCEPT TABLES
              sharded_events,
              sharded_session_replay_events,
              sharded_app_metrics,
              sharded_app_metrics2,
              sharded_log_entries,
              sharded_query_log_archive,
              sharded_ingestion_warnings,
              writable_events,
              events_recent
          TO S3('${URL}', '${GCS_HMAC_KEY}', '${GCS_HMAC_SECRET}')
          SETTINGS compression_method = 'zstd', compression_level = 6" \
    >/dev/null \
    || die "BACKUP statement failed -- inspect 'docker compose logs clickhouse'"

status=$(ch_query "SELECT status FROM system.backups WHERE name LIKE '%${TS}.zip%' ORDER BY start_time DESC LIMIT 1" | tr -d '[:space:]')
[ "$status" = "BACKUP_CREATED" ] \
    || die "BACKUP ended in status='${status}'"

size_bytes=$(aws_gcs ls --summarize "$(s3_uri "$KEY")" | awk '/^Total Size:/ {print $3; exit}')
log "Persons backup completed: $(numfmt --to=iec --suffix=B "${size_bytes:-0}")"
ok ch-persons
