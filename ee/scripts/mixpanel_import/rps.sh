#!/usr/bin/env bash
# Measure live consumer throughput (msg/sec) per Kafka consumer group by
# diffing CURRENT-OFFSET between two snapshots taken `--window` seconds apart.
#
# Usage:
#   ./rps.sh                              # default groups, 30s window
#   ./rps.sh -w 10                        # 10s window (faster, noisier)
#   ./rps.sh -w 60 grp1 grp2              # custom groups
#   ./rps.sh --watch                      # repeat every $WINDOW seconds (Ctrl-C to stop)
#
# Optional env:
#   COMPOSE_FILE   docker-compose.prod.yml
#   KAFKA_SERVICE  kafka

set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
KAFKA_SERVICE="${KAFKA_SERVICE:-kafka}"
WINDOW=30
WATCH=0

# Default groups: the three that matter for the migration pipeline.
DEFAULT_GROUPS=(clickhouse-ingestion-historical clickhouse-ingestion group1)

while [[ $# -gt 0 ]]; do
    case "$1" in
        -w|--window) WINDOW="$2"; shift 2 ;;
        --watch)     WATCH=1; shift ;;
        -h|--help)
            sed -n '2,12p' "$0"
            exit 0
            ;;
        *) break ;;
    esac
done

if [[ $# -gt 0 ]]; then
    GROUPS=("$@")
else
    GROUPS=("${DEFAULT_GROUPS[@]}")
fi

dc() { docker compose -f "$COMPOSE_FILE" "$@"; }

# Sum CURRENT-OFFSET across all partitions for a group. Skips rows with "-"
# (no committed offset yet — group joined but didn't consume).
sum_offsets() {
    dc exec -T "$KAFKA_SERVICE" rpk group describe "$1" 2>/dev/null \
        | awk '
            BEGIN { in_t=0; t=0 }
            /TOPIC[[:space:]]+PARTITION[[:space:]]+CURRENT-OFFSET/ { in_t=1; next }
            in_t && $3 ~ /^[0-9]+$/ { t += $3 }
            END { print t+0 }
        '
}

# Sum LAG across all partitions for a group. Same parsing as above but col 5.
sum_lag() {
    dc exec -T "$KAFKA_SERVICE" rpk group describe "$1" 2>/dev/null \
        | awk '
            BEGIN { in_t=0; t=0 }
            /TOPIC[[:space:]]+PARTITION[[:space:]]+CURRENT-OFFSET/ { in_t=1; next }
            in_t && NF >= 5 && $5 ~ /^[0-9]+$/ { t += $5 }
            END { print t+0 }
        '
}

if [[ -t 1 ]]; then
    BOLD=$'\033[1m'; DIM=$'\033[2m'; NC=$'\033[0m'
else
    BOLD=""; DIM=""; NC=""
fi

run_once() {
    declare -A before
    declare -A lag_before

    # Take all "before" snapshots first (fast, parallel-ish), then sleep, then "after".
    for g in "${GROUPS[@]}"; do
        before[$g]=$(sum_offsets "$g")
        lag_before[$g]=$(sum_lag "$g")
    done

    echo "${DIM}Sampling ${#GROUPS[@]} groups for ${WINDOW}s...${NC}"
    sleep "$WINDOW"

    printf "${BOLD}%-40s %14s %14s %12s${NC}\n" "GROUP" "MSGS/SEC" "TOTAL-LAG" "ETA"
    for g in "${GROUPS[@]}"; do
        after=$(sum_offsets "$g")
        lag_after=$(sum_lag "$g")
        delta=$((after - before[$g]))
        rps=$((delta / WINDOW))
        # ETA = current lag / current rps. If rps=0 -> stalled. If lag=0 -> done.
        if [[ "$lag_after" -eq 0 ]]; then
            eta="DONE"
        elif [[ "$rps" -le 0 ]]; then
            eta="STALLED"
        else
            secs_left=$((lag_after / rps))
            if   [[ $secs_left -lt 60    ]]; then eta="${secs_left}s"
            elif [[ $secs_left -lt 3600  ]]; then eta="$((secs_left/60))m"
            elif [[ $secs_left -lt 86400 ]]; then eta="$((secs_left/3600))h$(( (secs_left%3600)/60 ))m"
            else                                 eta="$((secs_left/86400))d$(( (secs_left%86400)/3600 ))h"
            fi
        fi
        printf "%-40s %14s %14s %12s\n" "$g" "$rps" "$lag_after" "$eta"
    done
}

if [[ "$WATCH" -eq 1 ]]; then
    while true; do
        run_once
        echo "---"
    done
else
    run_once
fi
