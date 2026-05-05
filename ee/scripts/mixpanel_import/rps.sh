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

set -eo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
KAFKA_SERVICE="${KAFKA_SERVICE:-kafka}"
WINDOW=30
WATCH=0

# Default groups — space-separated, parsed into positional args below if no
# explicit groups passed.
#
# IMPORTANT: do NOT name this variable GROUPS — that's a bash special readonly
# array containing the current user's OS group IDs (e.g. (0) for root), and
# all assignments to it are silently ignored. Bit me once already.
DEFAULT_KAFKA_GROUPS="clickhouse-ingestion-historical clickhouse-ingestion group1"

while [ "$#" -gt 0 ]; do
    case "$1" in
        -w|--window) WINDOW="$2"; shift 2 ;;
        --watch)     WATCH=1; shift ;;
        -h|--help)
            sed -n '2,12p' "$0"
            exit 0
            ;;
        --) shift; break ;;
        -*) echo "unknown option: $1" >&2; exit 64 ;;
        *)  break ;;
    esac
done

# If no group args left, seed positional params with the default group list.
if [ "$#" -eq 0 ]; then
    set -- $DEFAULT_KAFKA_GROUPS
fi

dc() { docker compose -f "$COMPOSE_FILE" "$@"; }

# Sum CURRENT-OFFSET across all partitions for a group. Skips rows with "-".
sum_offsets() {
    dc exec -T "$KAFKA_SERVICE" rpk group describe "$1" 2>/dev/null \
        | awk '
            BEGIN { in_t=0; t=0 }
            /TOPIC[[:space:]]+PARTITION[[:space:]]+CURRENT-OFFSET/ { in_t=1; next }
            in_t && $3 ~ /^[0-9]+$/ { t += $3 }
            END { print t+0 }
        '
}

sum_lag() {
    dc exec -T "$KAFKA_SERVICE" rpk group describe "$1" 2>/dev/null \
        | awk '
            BEGIN { in_t=0; t=0 }
            /TOPIC[[:space:]]+PARTITION[[:space:]]+CURRENT-OFFSET/ { in_t=1; next }
            in_t && NF >= 5 && $5 ~ /^[0-9]+$/ { t += $5 }
            END { print t+0 }
        '
}

if [ -t 1 ]; then
    BOLD=$'\033[1m'; DIM=$'\033[2m'; NC=$'\033[0m'
else
    BOLD=""; DIM=""; NC=""
fi

format_eta() {
    local lag=$1 rps=$2
    if [ "$lag" -eq 0 ]; then echo "DONE"; return; fi
    if [ "$rps" -le 0 ]; then echo "STALLED"; return; fi
    local s=$((lag / rps))
    if   [ "$s" -lt 60    ]; then echo "${s}s"
    elif [ "$s" -lt 3600  ]; then echo "$((s/60))m"
    elif [ "$s" -lt 86400 ]; then echo "$((s/3600))h$(( (s%3600)/60 ))m"
    else                          echo "$((s/86400))d$(( (s%86400)/3600 ))h"
    fi
}

run_once() {
    # Snapshots stored in two parallel temp files: line N = group N.
    local before_file after_file lag_after_file groups_file
    before_file=$(mktemp); after_file=$(mktemp)
    lag_after_file=$(mktemp); groups_file=$(mktemp)

    local count=0
    for g in "$@"; do
        echo "$g" >> "$groups_file"
        sum_offsets "$g" >> "$before_file"
        count=$((count + 1))
    done

    echo "${DIM}Sampling ${count} group(s) for ${WINDOW}s...${NC}"
    sleep "$WINDOW"

    for g in "$@"; do
        sum_offsets "$g" >> "$after_file"
        sum_lag "$g"     >> "$lag_after_file"
    done

    printf "${BOLD}%-40s %14s %14s %12s${NC}\n" "GROUP" "MSGS/SEC" "TOTAL-LAG" "ETA"

    local i=1
    while [ "$i" -le "$count" ]; do
        local g b a lag rps eta
        g=$(sed -n "${i}p"   "$groups_file")
        b=$(sed -n "${i}p"   "$before_file")
        a=$(sed -n "${i}p"   "$after_file")
        lag=$(sed -n "${i}p" "$lag_after_file")
        rps=$(( (a - b) / WINDOW ))
        eta=$(format_eta "$lag" "$rps")
        printf "%-40s %14s %14s %12s\n" "$g" "$rps" "$lag" "$eta"
        i=$((i + 1))
    done

    rm -f "$before_file" "$after_file" "$lag_after_file" "$groups_file"
}

if [ "$WATCH" -eq 1 ]; then
    while true; do
        run_once "$@"
        echo "---"
    done
else
    run_once "$@"
fi
