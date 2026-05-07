#!/usr/bin/env bash
# Prod status snapshot — host + container + Postgres + Kafka + ClickHouse +
# migration-specific state. Run from the repo root to confirm the system is
# in a sane state (pre/mid/post migration, or any time you want a snapshot).
#
# Usage (from repo root):
#   ./bin/docker-status.sh
#
# Optional env overrides:
#   COMPOSE_FILE        docker-compose.prod.yml
#   DB_SERVICE          db
#   POSTHOG_DB_USER     posthog
#   POSTHOG_DB_NAME     posthog
#   CH_SERVICE          clickhouse
#   CH_DATABASE         posthog
#   RPS_SECONDS         5      (Kafka lag sample window)
#   RPS_SCRIPT          ee/scripts/mixpanel_import/rps.sh  (path to lag tool)

set -uo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
DB_SERVICE="${DB_SERVICE:-db}"
POSTHOG_DB_USER="${POSTHOG_DB_USER:-posthog}"
POSTHOG_DB_NAME="${POSTHOG_DB_NAME:-posthog}"
CH_SERVICE="${CH_SERVICE:-clickhouse}"
CH_DATABASE="${CH_DATABASE:-posthog}"
RPS_SECONDS="${RPS_SECONDS:-5}"

HEADER='==========================================' 
hdr() { echo "$HEADER"; echo "$*"; echo "$HEADER"; }
dc() { docker compose -f "$COMPOSE_FILE" "$@"; }

hdr "1. Host resources"

cores=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo "?")
echo "--- Load avg / uptime  (cores: $cores) ---"
uptime
echo
echo "--- Memory  (want: 'available' >= 4 GB) ---"
free -gh
echo
echo "--- Disk    (want: data partition >= 30 GB free) ---"
df -h | grep -vE '^(tmpfs|udev|overlay)' | awk 'NR==1 || /\/$|var\/lib\/docker|posthog/'
echo
echo "--- Inodes  (want: <50%) ---"
df -i | grep -vE '^(tmpfs|udev|overlay)' | awk 'NR==1 || /\/$|var\/lib\/docker/'

echo
hdr "2. Container state"
dc ps --format "table {{.Name}}\t{{.Status}}\t{{.Service}}"

echo
hdr "3. Per-container CPU/RAM"
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}"

echo
hdr "4. Postgres connections  (want: <40)"
dc exec -T "$DB_SERVICE" psql -U "$POSTHOG_DB_USER" -d "$POSTHOG_DB_NAME" -A -t -c "
SELECT count(*) || ' connections, ' ||
       count(*) FILTER (WHERE state='active')   || ' active, ' ||
       count(*) FILTER (WHERE state='idle')      || ' idle, ' ||
       count(*) FILTER (WHERE state='idle in transaction') || ' idle-in-tx'
FROM pg_stat_activity WHERE datname='posthog';
"

echo
hdr "5. Kafka consumer lag  (sample ${RPS_SECONDS}s)"
RPS_SCRIPT="${RPS_SCRIPT:-ee/scripts/mixpanel_import/rps.sh}"
if [[ -x "$RPS_SCRIPT" ]]; then
    "$RPS_SCRIPT" -w "$RPS_SECONDS" || echo "(rps.sh failed — non-fatal)"
else
    echo "(rps.sh not found at $RPS_SCRIPT — skipping; set RPS_SCRIPT to override)"
fi

echo
hdr "6. ClickHouse merges  (want: 0-2 active, none long-running)"
dc exec -T "$CH_SERVICE" bash <<EOF
clickhouse-client --user default --password "\$CLICKHOUSE_PASSWORD" --database $CH_DATABASE <<'SQL'
SELECT
    count()                              AS active_merges,
    countIf(elapsed > 60)                AS long_running_merges,
    formatReadableSize(sum(memory_usage)) AS merge_mem
FROM system.merges
WHERE database = '$CH_DATABASE';
SQL
EOF

echo
hdr "7. Migration-specific state  (must be clean)"
echo "--- Pending BatchImport rows ---"
dc exec -T "$DB_SERVICE" psql -U "$POSTHOG_DB_USER" -d "$POSTHOG_DB_NAME" -c "
SELECT id, status, created_at
FROM posthog_batchimport
WHERE status NOT IN ('completed','failed')
ORDER BY created_at DESC
LIMIT 10;
"

echo "--- batch-import-worker container ---"
dc ps --all batch-import-worker | grep -v "^$" || echo "batch-import-worker: not present"

echo
hdr "DONE"
echo "Compare results against the 'status verdict' table in RUNBOOK.md."
