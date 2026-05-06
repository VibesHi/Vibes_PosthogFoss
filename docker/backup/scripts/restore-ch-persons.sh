#!/usr/bin/env bash
#
# restore-ch-persons.sh -- in-place destructive restore of all CH tables
# that were included in backup-ch-persons.sh (everything except the EXCEPT
# list below).
#
# Usage:
#   restore-ch-persons.sh                                 # most recent backup
#   restore-ch-persons.sh latest                          # same
#   restore-ch-persons.sh clickhouse/persons-weekly/<TS>.zip
#
# Restore semantics: REPLACE, not MERGE.
#
#   The naive `RESTORE DATABASE ... allow_non_empty_tables=true` ATTACHES
#   the backup's parts to the live tables and leaves old data behind --
#   the result is `live ∪ backup`, NOT a rewind. For ReplacingMergeTree
#   tables this is wrong: rows that existed in live but were absent from
#   the backup persist.
#
#   So this script does:
#       1. Enumerate every table in the `posthog` CH database that ISN'T
#          in the backup-ch-persons.sh EXCEPT list (i.e. every table that
#          WOULD be in the backup).
#       2. DROP each of those tables (SYNC).
#       3. `RESTORE DATABASE posthog FROM ...` -- CH recreates the tables
#          from the backup's metadata + data. No `allow_non_empty_tables`.
#
# Known limitation: a table that exists in the LIVE database but was NOT
# in the backup (e.g. added by a posthog version upgrade between backup
# and now) WILL be dropped here even though RESTORE won't recreate it.
# Inspect `unzip -l <backup.zip>` against `clickhouse/persons-weekly/...`
# manually if you need finer control.
#
# DESTRUCTIVE: caller MUST stop dependent services first -- otherwise
# web/worker queries will hit non-existent tables during the gap between
# DROP and RESTORE. Same operational model as restore-pg.sh.

SCRIPT_NAME=restore-ch-persons
# shellcheck source=_lib.sh
source "$(dirname "$0")/_lib.sh"

require_gcs_creds
require_ch_creds

ARG="${1:-latest}"

# --- pre-flight: refuse on orphan parallel DBs holding dictionaries ----------
# Orphan parallel DBs (e.g. `posthog_verify` left behind by a failed AS-clause
# restore drill) can hold Dictionary-engine tables whose source clauses still
# reference `posthog.<source_table>`. CH refuses DROP TABLE on the source
# while any Dictionary anywhere depends on it (HAVE_DEPENDENT_OBJECTS, code
# 630). Detect upfront and refuse to start so we don't get half-way through
# a destructive drop loop and stall.
log "Checking for orphan databases with Dictionary tables..."
assert_no_cross_db_dict_deps_on_posthog

# Mirror of the EXCEPT list in backup-ch-persons.sh. MUST stay in sync --
# any table here is NOT touched by drop OR restore. If you change one, change
# the other.
EXCEPT_TABLES_SQL="('sharded_events','sharded_session_replay_events','sharded_app_metrics','sharded_app_metrics2','sharded_log_entries','sharded_query_log_archive','sharded_ingestion_warnings','writable_events','events_recent')"

# --- resolve source key ------------------------------------------------------
if [ "$ARG" = "latest" ]; then
    log "Listing clickhouse/persons-weekly/ in GCS to find the most recent backup..."
    KEY=$(aws_gcs ls "$(s3_uri "clickhouse/persons-weekly/")" \
        | awk '/\.zip$/ {print $4}' \
        | sort \
        | tail -n 1)
    [ -n "$KEY" ] || die "No persons-weekly *.zip found in s3://$(gcs_bucket_name)/clickhouse/persons-weekly/"
    KEY="clickhouse/persons-weekly/${KEY}"
elif [[ "$ARG" == clickhouse/persons-weekly/* ]]; then
    KEY="$ARG"
elif [[ "$ARG" == s3://* ]]; then
    # Convert back to bucket-relative key
    KEY="${ARG#s3://}"
    KEY="${KEY#$(gcs_bucket_name)/}"
else
    die "Unrecognized argument: '${ARG}'."
fi

gcs_object_exists "$(s3_uri "$KEY")" \
    || die "Source key not found in GCS: $(s3_uri "$KEY")"

SRC_HTTP=$(http_uri "$KEY")
log "Source: ${SRC_HTTP}"

# --- enumerate tables we'll drop ---------------------------------------------
# Use FORMAT TabSeparated -> one table name per line, no quoting.
#
# Dictionaries (engine='Dictionary') hold a hard dependency on their source
# tables -- CH refuses `DROP TABLE` on a source while a dictionary still
# references it (HAVE_DEPENDENT_OBJECTS, code 630). MaterializedViews,
# Distributed, and Buffer engines don't have this problem.
#
# Two-phase drop: dictionaries first, then everything else. This works
# regardless of alphabetical name ordering between the source and the dict.
log "Enumerating posthog DB tables that are in the backup's scope..."
DICTS_TO_DROP=$(ch_query "SELECT name FROM system.tables
                          WHERE database='posthog'
                            AND engine='Dictionary'
                            AND name NOT IN ${EXCEPT_TABLES_SQL}
                          ORDER BY name
                          FORMAT TabSeparated")
TABLES_TO_DROP=$(ch_query "SELECT name FROM system.tables
                           WHERE database='posthog'
                             AND engine != 'Dictionary'
                             AND name NOT IN ${EXCEPT_TABLES_SQL}
                           ORDER BY name
                           FORMAT TabSeparated")
dict_count=$(echo "$DICTS_TO_DROP" | grep -c . || true)
table_count=$(echo "$TABLES_TO_DROP" | grep -c . || true)
total=$((dict_count + table_count))
[ "$total" -gt 0 ] || die "No tables to drop in posthog DB. Is CH up? Is the database empty? Inspect 'docker compose logs clickhouse'."

log "Will drop ${dict_count} dictionar(ies) + ${table_count} table(s) = ${total} object(s)."
log "Sample tables:"
echo "$TABLES_TO_DROP" | sed 's/^/    /' | head -n 30
if [ "$table_count" -gt 30 ]; then
    log "  ... and $((table_count - 30)) more tables"
fi
if [ "$dict_count" -gt 0 ]; then
    log "Dictionaries (dropped first):"
    echo "$DICTS_TO_DROP" | sed 's/^/    /'
fi

# --- confirmation prompt -----------------------------------------------------
confirm_destructive "About to DROP the ${total} objects listed above and
RESTORE them from ${SRC_HTTP}.

EXCLUDED (NOT touched): sharded_events, session_replay, app_metrics(_2),
log_entries, query_log_archive, ingestion_warnings, writable_events, events_recent.

This is IN-PLACE on the LIVE ClickHouse instance. There is NO rollback path
once DROP runs -- if RESTORE fails after DROP, the affected objects will be
GONE until you re-run with a working source."

# --- drop dictionaries first -------------------------------------------------
# Must drop dictionaries before their source tables. CH refuses to DROP TABLE
# while a Dictionary engine still references it (HAVE_DEPENDENT_OBJECTS).
if [ "$dict_count" -gt 0 ]; then
    log "Dropping ${dict_count} dictionar(ies)..."
    while IFS= read -r dict; do
        [ -n "$dict" ] || continue
        ch_query "DROP DICTIONARY IF EXISTS posthog.\`${dict}\` SYNC" >/dev/null \
            || die "DROP DICTIONARY posthog.${dict} failed -- DB is now in an inconsistent state. Re-run RESTORE from ${SRC_HTTP} to recover."
    done <<< "$DICTS_TO_DROP"
fi

# --- drop remaining tables ----------------------------------------------------
log "Dropping ${table_count} table(s)..."
while IFS= read -r tbl; do
    [ -n "$tbl" ] || continue
    ch_query "DROP TABLE IF EXISTS posthog.\`${tbl}\` SYNC" >/dev/null \
        || die "DROP TABLE posthog.${tbl} failed -- DB is now in an inconsistent state. Re-run RESTORE from ${SRC_HTTP} to recover."
done <<< "$TABLES_TO_DROP"

# --- restore -----------------------------------------------------------------
log "Restoring..."
ch_query "RESTORE DATABASE posthog
          FROM S3('${SRC_HTTP}', '${GCS_HMAC_KEY}', '${GCS_HMAC_SECRET}')" \
    >/dev/null \
    || die "RESTORE statement failed -- ${table_count} tables in posthog DB are now MISSING. Re-run with a known-good source. Inspect 'docker compose logs clickhouse'."

status=$(ch_query "SELECT status FROM system.backups ORDER BY start_time DESC LIMIT 1" | tr -d '[:space:]')
[ "$status" = "RESTORED" ] || die "RESTORE ended in status='${status}' -- some tables may be missing or partially restored."

restored_count=$(ch_query "SELECT count() FROM system.tables
                           WHERE database='posthog'
                             AND name NOT IN ${EXCEPT_TABLES_SQL}" | tr -d '[:space:]')
log "Restore complete. ${restored_count} tables/dictionaries present (was ${total} before drop)."
ok restore-ch-persons
