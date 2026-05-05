#!/usr/bin/env bash
# Toggle PostHog "personless mode" (Team.person_processing_opt_out) for a team
# during a one-shot historical migration.
#
# When ON, the ingestion-general consumer skips person creation/updates for
# every event ingested for that team — typically 3–10x consumer throughput on
# bulk imports. Live SDK traffic to the same team also goes personless while
# the flag is on, so flip it OFF before redirecting production traffic.
#
# What this script does:
#   1. Reads the current value of posthog_team.person_processing_opt_out
#   2. (on/off only) Updates the row
#   3. Restarts ingestion-general so the team-config cache reloads immediately
#      instead of waiting for the next refresh cycle (~2 min)
#
# All values come from the env or CLI args — no hardcoded team_id, service
# name, or db credentials in this file.
#
# Usage:
#   ./personless_mode.sh status <team_id>
#   ./personless_mode.sh on     <team_id>
#   ./personless_mode.sh off    <team_id>
#
# Optional env overrides (defaults match this repo's docker-compose.prod.yml):
#   COMPOSE_FILE        docker-compose.prod.yml
#   INGESTION_SERVICE   ingestion-general
#   DB_SERVICE          db
#   POSTHOG_DB_USER     posthog
#   POSTHOG_DB_NAME     posthog
#   SKIP_RESTART        set to 1 to skip the ingestion-general restart

set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
INGESTION_SERVICE="${INGESTION_SERVICE:-ingestion-general}"
DB_SERVICE="${DB_SERVICE:-db}"
POSTHOG_DB_USER="${POSTHOG_DB_USER:-posthog}"
POSTHOG_DB_NAME="${POSTHOG_DB_NAME:-posthog}"
SKIP_RESTART="${SKIP_RESTART:-0}"

action="${1:-}"
team_id="${2:-}"

if [[ -z "$action" || -z "$team_id" ]]; then
    echo "usage: $0 {status|on|off} <team_id>" >&2
    exit 64
fi

case "$action" in
    status|on|off) ;;
    *) echo "invalid action: $action (use status|on|off)" >&2; exit 64 ;;
esac

if ! [[ "$team_id" =~ ^[0-9]+$ ]]; then
    echo "team_id must be a positive integer, got: $team_id" >&2
    exit 64
fi

dc() {
    docker compose -f "$COMPOSE_FILE" "$@"
}

# -A -t = unaligned, tuples-only — easy to parse
psql_q() {
    dc exec -T "$DB_SERVICE" psql -U "$POSTHOG_DB_USER" -d "$POSTHOG_DB_NAME" -A -t -c "$1"
}

print_state() {
    local row
    row="$(psql_q "
        SELECT id,
               name,
               COALESCE(person_processing_opt_out, FALSE)::text
          FROM posthog_team
         WHERE id = $team_id;
    ")"
    if [[ -z "$row" ]]; then
        echo "team $team_id not found in posthog_team" >&2
        exit 1
    fi
    IFS='|' read -r id name flag <<< "$row"
    echo "team_id=$id  name='$name'  personless=$flag"
}

case "$action" in
    status)
        print_state
        ;;

    on|off)
        target_flag="FALSE"
        [[ "$action" == "on" ]] && target_flag="TRUE"

        echo "before:"
        print_state

        # Idempotent — UPDATE with same value is a no-op for downstream behaviour
        psql_q "
            UPDATE posthog_team
               SET person_processing_opt_out = $target_flag
             WHERE id = $team_id;
        " >/dev/null

        echo "after:"
        print_state

        if [[ "$SKIP_RESTART" == "1" ]]; then
            echo "skipping $INGESTION_SERVICE restart (SKIP_RESTART=1)"
            echo "team-config cache will reload on its own within ~2 min"
        else
            echo "restarting $INGESTION_SERVICE so team-config cache reloads..."
            dc restart "$INGESTION_SERVICE"
            echo "done. wait ~30s for kafka rebalance before measuring throughput."
        fi
        ;;
esac
