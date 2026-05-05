#!/usr/bin/env bash
# Disable/enable hog function transformations (GeoIP, custom transforms) for a
# team during a one-shot historical migration. Each enabled transformation
# spins up a hog VM per event in ingestion-general — disabling them lets the
# transformer short-circuit at the `teamHogFunctions.length === 0` check
# (see nodejs/src/cdp/hog-transformations/hog-transformer.service.ts).
#
# Reversible: `off` writes the previously-enabled IDs to a state file and
# `on` re-enables exactly those (so we don't accidentally enable transforms
# the operator had already disabled for unrelated reasons).
#
# Usage:
#   ./transformations_mode.sh status <team_id>
#   ./transformations_mode.sh off    <team_id>
#   ./transformations_mode.sh on     <team_id>
#
# Optional env overrides:
#   COMPOSE_FILE        docker-compose.prod.yml
#   INGESTION_SERVICE   ingestion-general
#   DB_SERVICE          db
#   POSTHOG_DB_USER     posthog
#   POSTHOG_DB_NAME     posthog
#   STATE_DIR           /var/tmp/posthog-mixpanel-import
#   SKIP_RESTART        set to 1 to skip the ingestion-general restart

set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
INGESTION_SERVICE="${INGESTION_SERVICE:-ingestion-general}"
DB_SERVICE="${DB_SERVICE:-db}"
POSTHOG_DB_USER="${POSTHOG_DB_USER:-posthog}"
POSTHOG_DB_NAME="${POSTHOG_DB_NAME:-posthog}"
STATE_DIR="${STATE_DIR:-/var/tmp/posthog-mixpanel-import}"
SKIP_RESTART="${SKIP_RESTART:-0}"

action="${1:-}"
team_id="${2:-}"

if [[ -z "$action" || -z "$team_id" ]]; then
    echo "usage: $0 {status|off|on} <team_id>" >&2
    exit 64
fi

case "$action" in
    status|off|on) ;;
    *) echo "invalid action: $action (use status|off|on)" >&2; exit 64 ;;
esac

if ! [[ "$team_id" =~ ^[0-9]+$ ]]; then
    echo "team_id must be a positive integer, got: $team_id" >&2
    exit 64
fi

mkdir -p "$STATE_DIR"
state_file="$STATE_DIR/team-${team_id}-disabled-transformations.txt"

dc() {
    docker compose -f "$COMPOSE_FILE" "$@"
}

psql_q() {
    dc exec -T "$DB_SERVICE" psql -U "$POSTHOG_DB_USER" -d "$POSTHOG_DB_NAME" -A -t -c "$1"
}

list_transformations() {
    psql_q "
        SELECT id::text || E'\t' || name || E'\t' || enabled::text
          FROM posthog_hogfunction
         WHERE team_id = $team_id
           AND type = 'transformation'
           AND deleted = FALSE
         ORDER BY enabled DESC, name ASC;
    "
}

case "$action" in
    status)
        rows="$(list_transformations)"
        if [[ -z "$rows" ]]; then
            echo "team $team_id has no transformations"
            exit 0
        fi
        echo -e "id\tname\tenabled"
        echo "$rows"
        if [[ -f "$state_file" ]]; then
            echo
            echo "state file present at $state_file:"
            wc -l < "$state_file" | awk '{print "  " $1 " transformation id(s) saved (will be re-enabled on `on`)"}'
        fi
        ;;

    off)
        # Snapshot currently-enabled transformation ids BEFORE disabling
        enabled_ids="$(psql_q "
            SELECT id
              FROM posthog_hogfunction
             WHERE team_id = $team_id
               AND type = 'transformation'
               AND enabled = TRUE
               AND deleted = FALSE;
        " | sed '/^$/d')"

        if [[ -z "$enabled_ids" ]]; then
            echo "team $team_id has no enabled transformations — nothing to do"
            exit 0
        fi

        echo "$enabled_ids" > "$state_file"
        echo "saved $(wc -l < "$state_file" | tr -d ' ') id(s) to $state_file:"
        sed 's/^/  /' "$state_file"

        # Bulk disable
        psql_q "
            UPDATE posthog_hogfunction
               SET enabled = FALSE
             WHERE team_id = $team_id
               AND type = 'transformation'
               AND enabled = TRUE;
        " >/dev/null

        echo "disabled. current state:"
        list_transformations | awk -F'\t' '{printf "  %s  %s  enabled=%s\n", $1, $2, $3}'
        ;;

    on)
        if [[ ! -f "$state_file" ]]; then
            echo "no state file at $state_file — nothing to re-enable" >&2
            echo "(if you disabled transformations a different way, re-enable them" >&2
            echo " manually from the PostHog UI: Data pipelines → Transformations)" >&2
            exit 1
        fi

        # Read ids and build a quoted IN-list. Each id is a UUID string.
        ids="$(awk 'NF' "$state_file" | awk '{printf "'\''%s'\'',", $0}' | sed 's/,$//')"
        if [[ -z "$ids" ]]; then
            echo "state file is empty — nothing to re-enable"
            rm -f "$state_file"
            exit 0
        fi

        psql_q "
            UPDATE posthog_hogfunction
               SET enabled = TRUE
             WHERE team_id = $team_id
               AND type = 'transformation'
               AND id IN ($ids);
        " >/dev/null

        echo "re-enabled. current state:"
        list_transformations | awk -F'\t' '{printf "  %s  %s  enabled=%s\n", $1, $2, $3}'

        rm -f "$state_file"
        echo "removed state file $state_file"
        ;;
esac

if [[ "$action" != "status" ]]; then
    if [[ "$SKIP_RESTART" == "1" ]]; then
        echo "skipping $INGESTION_SERVICE restart (SKIP_RESTART=1)"
        echo "team-config cache will reload on its own within ~2 min"
    else
        echo "restarting $INGESTION_SERVICE so hog-function cache reloads..."
        dc restart "$INGESTION_SERVICE"
        echo "done. wait ~30s for kafka rebalance before measuring throughput."
    fi
fi
