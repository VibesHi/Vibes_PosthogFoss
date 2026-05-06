#!/usr/bin/env bash
# Pre-revert sanity check for the Mixpanel migration.
#
# Verifies four things before you tear down migration containers / undo
# personless-mode / re-enable transformations:
#
#   1. clickhouse-ingestion-historical group:    TOTAL-LAG ≈ 0
#   2. clickhouse-ingestion           group:    TOTAL-LAG ≈ 0
#   3. group1 (CH StorageKafka consumer) lag ≈ 0 on clickhouse_events_json
#   4. Per-day count(*) in CH for the migration team matches the source
#      Mixpanel daily files (gs://$GCS_BUCKET/$GCS_PREFIX/YYYY-MM-DD.jsonl.gz)
#      to within $TOLERANCE_PCT (default 1%).
#
# Exits 0 on success, 1 on any failure / mismatch. Prints a per-day table
# so it's obvious which day(s) didn't fully ingest.
#
# Usage:
#   ./migration_complete_check.sh <team_id> <YYYY-MM> [YYYY-MM ...]
#
# Examples:
#   # Just the lag checks (no months)
#   ./migration_complete_check.sh 3
#
#   # Lag + per-day count for one month
#   ./migration_complete_check.sh 3 2024-03
#
#   # Lag + per-day count for several months
#   ./migration_complete_check.sh 3 2024-01 2024-02 2024-03
#
# Optional env overrides:
#   COMPOSE_FILE        docker-compose.prod.yml
#   KAFKA_SERVICE       kafka
#   CH_SERVICE          clickhouse
#   CH_DATABASE         posthog
#   CH_TABLE            events            (distributed view; raw is sharded_events)
#   CLICKHOUSE_PASSWORD read from .env if unset
#   GCS_BUCKET          posthog-helper-bucket
#   GCS_PREFIX          mixpanel-daily
#   TOLERANCE_PCT       1                 (per-day count diff tolerance)
#   LAG_OK_THRESHOLD    100               (warn instead of fail if 0 < lag <= this)
#   PARALLEL            8                 (gsutil concurrent file counts)

set -euo pipefail

# ------------- args / config -------------
team_id="${1:-}"
shift || true
months=("$@")

if [[ -z "$team_id" ]]; then
    echo "usage: $0 <team_id> [YYYY-MM ...]" >&2
    exit 64
fi
if ! [[ "$team_id" =~ ^[0-9]+$ ]]; then
    echo "team_id must be a positive integer, got: $team_id" >&2
    exit 64
fi
for m in "${months[@]}"; do
    if ! [[ "$m" =~ ^[0-9]{4}-[0-9]{2}$ ]]; then
        echo "month must be YYYY-MM, got: $m" >&2
        exit 64
    fi
done

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
KAFKA_SERVICE="${KAFKA_SERVICE:-kafka}"
CH_SERVICE="${CH_SERVICE:-clickhouse}"
CH_DATABASE="${CH_DATABASE:-posthog}"
CH_TABLE="${CH_TABLE:-events}"
GCS_BUCKET="${GCS_BUCKET:-posthog-helper-bucket}"
GCS_PREFIX="${GCS_PREFIX:-mixpanel-daily}"
TOLERANCE_PCT="${TOLERANCE_PCT:-1}"
LAG_OK_THRESHOLD="${LAG_OK_THRESHOLD:-100}"
PARALLEL="${PARALLEL:-8}"

if [[ -z "${CLICKHOUSE_PASSWORD:-}" ]]; then
    if [[ -f .env ]]; then
        CLICKHOUSE_PASSWORD="$(awk -F= '/^CLICKHOUSE_PASSWORD=/{sub(/^CLICKHOUSE_PASSWORD=/,""); print; exit}' .env)"
    fi
fi
if [[ -z "${CLICKHOUSE_PASSWORD:-}" ]]; then
    echo "CLICKHOUSE_PASSWORD not set and not found in .env" >&2
    exit 64
fi

# ------------- pretty output -------------
if [[ -t 1 ]]; then
    RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'; BOLD=$'\033[1m'; NC=$'\033[0m'
else
    RED=""; GREEN=""; YELLOW=""; BOLD=""; NC=""
fi
ok()   { printf "%b%-4s%b  %s\n" "$GREEN" "OK"   "$NC" "$*"; }
warn() { printf "%b%-4s%b  %s\n" "$YELLOW" "WARN" "$NC" "$*"; }
fail() { printf "%b%-4s%b  %s\n" "$RED"   "FAIL" "$NC" "$*"; }
hdr()  { printf "\n%b== %s ==%b\n" "$BOLD" "$*" "$NC"; }

EXIT_CODE=0
mark_fail() { EXIT_CODE=1; }

dc() { docker compose -f "$COMPOSE_FILE" "$@"; }

# ------------- 1-3. Kafka lag checks -------------
#
# Naive lag (the number `rpk group describe` shows as TOTAL-LAG) is just
#     LAG = LOG-END-OFFSET - CURRENT-OFFSET
# which doesn't distinguish:
#   (a) real pending: consumer is behind, data still on disk
#   (b) phantom: data was deleted by retention before the consumer caught up;
#       CURRENT-OFFSET still points at deleted offsets so the math says "lag"
#       but there's nothing to consume. The committed offset never advances
#       because there are no messages to process and commit.
#
# We compute both separately by parsing the per-partition table:
#   real_lag    = sum(max(0, LOG-END - max(CURRENT, LOG-START)))
#   phantom_lag = sum(max(0, LOG-START - CURRENT))
#
# real_lag = 0 means the group is functionally caught up, even if rpk shows
# nonzero TOTAL-LAG.

# Returns "<real_lag> <phantom_lag>" on stdout, or "MISSING" if the group
# doesn't exist.
lag_for_group() {
    local group=$1
    local out
    out=$(dc exec -T "$KAFKA_SERVICE" rpk group describe "$group" 2>/dev/null || true)
    if [[ -z "$out" ]]; then
        echo "MISSING"
        return
    fi
    awk '
        BEGIN { in_table=0; cur_col=0; start_col=0; end_col=0; real=0; phantom=0 }
        /TOPIC[[:space:]]+PARTITION[[:space:]]+CURRENT-OFFSET/ {
            in_table=1
            for (i=1; i<=NF; i++) {
                if ($i == "CURRENT-OFFSET")   cur_col=i
                if ($i == "LOG-START-OFFSET") start_col=i
                if ($i == "LOG-END-OFFSET")   end_col=i
            }
            next
        }
        in_table && cur_col > 0 && end_col > 0 && $cur_col ~ /^[0-9]+$/ {
            cur = $cur_col + 0
            end = $end_col + 0
            # LOG-START-OFFSET column may be absent in older rpk versions; if
            # so, default to 0 (then phantom is always 0 and real == raw lag).
            start = (start_col > 0 && $start_col ~ /^[0-9]+$/) ? $start_col + 0 : 0
            effective = (cur > start) ? cur : start
            r = end - effective; if (r < 0) r = 0
            p = start - cur;     if (p < 0) p = 0
            real += r; phantom += p
        }
        END { print real " " phantom }
    ' <<<"$out"
}

check_group() {
    local group=$1
    local res real_lag phantom_lag
    res=$(lag_for_group "$group")
    if [[ "$res" == "MISSING" ]]; then
        warn "group '$group': not found (skip — only matters if you produced to it)"
        return
    fi
    real_lag=$(awk '{print $1+0}' <<<"$res")
    phantom_lag=$(awk '{print $2+0}' <<<"$res")

    if [[ "$real_lag" -eq 0 && "$phantom_lag" -eq 0 ]]; then
        ok "group '$group': fully caught up (real_lag=0, no retention loss)"
    elif [[ "$real_lag" -eq 0 && "$phantom_lag" -gt 0 ]]; then
        warn "group '$group': real_lag=0 (caught up) BUT phantom_lag=$phantom_lag — retention deleted those events before the consumer drained them. Real damage to assess: run with the relevant YYYY-MM args."
    elif [[ "$real_lag" -le "$LAG_OK_THRESHOLD" && "$phantom_lag" -eq 0 ]]; then
        warn "group '$group': real_lag=$real_lag (≤ $LAG_OK_THRESHOLD, likely live trickle — re-run in 30s to confirm draining)"
    elif [[ "$real_lag" -gt "$LAG_OK_THRESHOLD" ]]; then
        fail "group '$group': real_lag=$real_lag — wait for it to drain before reverting (phantom_lag=$phantom_lag)"
        mark_fail
    else
        warn "group '$group': real_lag=$real_lag, phantom_lag=$phantom_lag"
    fi
}

hdr "1-3. Kafka consumer-group lag"
check_group clickhouse-ingestion-historical
check_group clickhouse-ingestion
check_group group1

# ------------- 4. Per-day count comparison -------------

if [[ ${#months[@]} -eq 0 ]]; then
    hdr "4. Per-day count comparison: SKIPPED (no months passed)"
    echo "(pass YYYY-MM args to enable, e.g. $0 $team_id 2024-03)"
else
    ch_query() {
        dc exec -T "$CH_SERVICE" clickhouse-client \
            --user default --password "$CLICKHOUSE_PASSWORD" \
            --database "$CH_DATABASE" \
            -q "$1"
    }

    count_one_gcs_file() {
        local uri=$1
        local day cnt
        day=$(basename "$uri" .jsonl.gz)
        # If gunzip fails (corrupt file), wc -l still prints 0 — flag explicitly.
        if cnt=$(gsutil cat "$uri" 2>/dev/null | gunzip 2>/dev/null | wc -l); then
            cnt=${cnt// /}
            printf "%s\t%s\n" "$day" "$cnt"
        else
            printf "%s\tERROR\n" "$day"
        fi
    }
    export -f count_one_gcs_file

    for month in "${months[@]}"; do
        hdr "4. Month $month"

        # ---- ClickHouse counts (one query per month) ----
        ch_out=$(ch_query "
            SELECT toDate(timestamp) AS day, count() AS cnt
              FROM ${CH_DATABASE}.${CH_TABLE}
             WHERE team_id = $team_id
               AND timestamp >= toDate('${month}-01')
               AND timestamp <  toDate('${month}-01') + INTERVAL 1 MONTH
             GROUP BY day
             ORDER BY day
             FORMAT TabSeparated
        ")

        declare -A ch_count=()
        while IFS=$'\t' read -r day cnt; do
            [[ -n "${day:-}" ]] && ch_count[$day]=$cnt
        done <<<"$ch_out"

        # ---- Source counts (parallel gsutil) ----
        # Try two layout conventions before giving up:
        #   (a) ${GCS_PREFIX}/${month}-*.jsonl.gz  e.g. mixpanel-daily/2024-03-*.jsonl.gz
        #   (b) ${month//-//}/${month}-*.jsonl.gz  e.g. 2024/03/2024-03-*.jsonl.gz
        # (b) is the worker-friendly hierarchy used by the splitter for new months.
        month_path="${month//-//}"  # 2024-03 -> 2024/03
        glob_a="gs://${GCS_BUCKET}/${GCS_PREFIX}/${month}-*.jsonl.gz"
        glob_b="gs://${GCS_BUCKET}/${month_path}/${month}-*.jsonl.gz"
        files=$(gsutil ls "$glob_a" 2>/dev/null || true)
        if [[ -z "$files" ]]; then
            files=$(gsutil ls "$glob_b" 2>/dev/null || true)
        fi
        if [[ -z "$files" ]]; then
            fail "no GCS files matched either layout: '$glob_a' or '$glob_b'. Set GCS_PREFIX explicitly if your layout differs."
            mark_fail
            continue
        fi

        echo "Counting source lines for $(echo "$files" | wc -l | tr -d ' ') file(s) with $PARALLEL parallel workers (this is the slow step)..."

        src_tmp=$(mktemp)
        # `bash -c` because exported functions need bash, not sh.
        echo "$files" \
            | xargs -n1 -P "$PARALLEL" -I{} bash -c 'count_one_gcs_file "$@"' _ {} \
            > "$src_tmp"

        # ---- Per-day comparison table ----
        printf "\n%-12s %14s %14s %10s  %s\n" "DAY" "SOURCE" "CH" "DIFF%" "STATUS"
        printf "%-12s %14s %14s %10s  %s\n" "---" "------" "--" "-----" "------"

        # Sort src_tmp by day for stable output.
        month_total_src=0
        month_total_ch=0
        while IFS=$'\t' read -r day src; do
            [[ -z "$day" ]] && continue
            ch="${ch_count[$day]:-0}"
            if [[ "$src" == "ERROR" ]]; then
                printf "%-12s %14s %14s %10s  %b\n" "$day" "ERROR" "$ch" "n/a" "${RED}READ-FAIL${NC}"
                mark_fail
                continue
            fi
            month_total_src=$((month_total_src + src))
            month_total_ch=$((month_total_ch + ch))
            if [[ "$src" -eq 0 ]]; then
                printf "%-12s %14s %14s %10s  %b\n" "$day" "$src" "$ch" "n/a" "${YELLOW}EMPTY${NC}"
                continue
            fi
            diff_pct=$(awk -v s="$src" -v c="$ch" 'BEGIN { printf "%.2f", (c - s) * 100.0 / s }')
            abs=$(awk -v d="$diff_pct" 'BEGIN { print (d<0?-d:d) }')
            if awk -v a="$abs" -v t="$TOLERANCE_PCT" 'BEGIN { exit !(a > t) }'; then
                status="${RED}MISMATCH${NC}"
                mark_fail
            else
                status="${GREEN}OK${NC}"
            fi
            printf "%-12s %14s %14s %10s  %b\n" "$day" "$src" "$ch" "$diff_pct%" "$status"
        done < <(sort "$src_tmp")

        rm -f "$src_tmp"

        # Month total summary line.
        if [[ "$month_total_src" -gt 0 ]]; then
            month_diff=$(awk -v s="$month_total_src" -v c="$month_total_ch" 'BEGIN { printf "%.2f", (c - s) * 100.0 / s }')
            printf "%-12s %14s %14s %10s\n" "TOTAL" "$month_total_src" "$month_total_ch" "$month_diff%"
        fi
        unset ch_count
    done
fi

echo
if [[ $EXIT_CODE -eq 0 ]]; then
    printf "%bAll checks passed — safe to revert migration containers/config.%b\n" "$GREEN" "$NC"
else
    printf "%bOne or more checks failed — see above. Do NOT revert yet.%b\n" "$RED" "$NC"
fi
exit $EXIT_CODE
