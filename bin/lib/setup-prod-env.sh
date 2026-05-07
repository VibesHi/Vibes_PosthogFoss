#!/usr/bin/env bash
#
# Phase 1: validate + source .env. Pure read; no mutation.
#
# What this used to do that it no longer does:
#   * auto-write CADDY_HOST / CADDY_TLS_BLOCK derived from DOMAIN / TLS_BLOCK
#   * auto-write a "throughput defaults" block (NGINX_UNIT_APP_PROCESSES,
#     WEB_CONCURRENCY, KAFKA_INGESTION_PARTITIONS, MULTI_ORG_ENABLED,
#     DEPLOYMENT, PERSON_ON_EVENTS_V2_OVERRIDE, PERSISTED_FEATURE_FLAGS, etc.)
#
# Both blocks were dead code in the documented `cp .env.example.prod .env`
# bootstrap flow — those keys already live in the template with the same
# values. Dual sources of truth invited drift; the schema-drift warning below
# already catches the "pulled new code, forgot to merge new vars" case.
#
# All those keys (including CADDY_HOST and CADDY_TLS_BLOCK) now live exclusively
# in .env.example.prod. Edit them there once after `cp`, never again.

phase_validate_env() {
    [ -f .env ] || die ".env not found. Bootstrap with:
    cp .env.example.prod .env
    \$EDITOR .env"

    # Catch CHANGE_ME_ both bare (`KEY=CHANGE_ME_x`) and inside quotes
    # (`KEY="CHANGE_ME_x, suffix"`) -- the quoted form is used for values that
    # contain commas/spaces (e.g. CADDY_HOST) and would otherwise slip past a
    # bare-anchor regex.
    if grep -qE '^[A-Z_][A-Z0-9_]*="?CHANGE_ME_' .env; then
        grep -nE '^[A-Z_][A-Z0-9_]*="?CHANGE_ME_' .env >&2
        die ".env contains CHANGE_ME_ placeholders. Fill them in before running."
    fi

    # Source .env into our shell so compose subcommands inherit every var.
    _source_env .env

    local var val
    for var in DOMAIN POSTHOG_SECRET ENCRYPTION_SALT_KEYS \
               POSTHOG_DB_PASSWORD OBJECT_STORAGE_PASSWORD CLICKHOUSE_PASSWORD; do
        [ -n "${!var:-}" ] || die ".env: $var is empty"
    done

    for var in POSTHOG_DB_PASSWORD OBJECT_STORAGE_PASSWORD CLICKHOUSE_PASSWORD; do
        val="${!var}"
        if [ "${#val}" -lt 16 ]; then
            warn ".env: $var is only ${#val} chars — regenerate with 'openssl rand -hex 24'"
        fi
    done

    # CADDY sanity: CADDY_HOST is referenced inside docker-compose.base.yml's
    # CADDYFILE: env value as ${CADDY_HOST:-http://localhost:8000}. Compose
    # substitutes that at YAML parse time using the host shell + .env (NOT the
    # container's environment: block, which arrives too late). If CADDY_HOST is
    # missing, Caddy boots with the localhost fallback and never gets a TLS
    # cert for $DOMAIN. Cheap guard — fail fast rather than discover a TLS
    # outage 15 min after the stack comes up.
    [ -n "${CADDY_HOST:-}" ] || die ".env: CADDY_HOST is empty. Set to '\$DOMAIN, http://, https://' (literal — no shell expansion in .env)."

    # Schema drift: warn (don't die) if .env.example.prod has keys missing
    # from .env. Catches the common "I git pull'd, forgot to merge new vars".
    local missing="" key
    while IFS= read -r key; do
        grep -qE "^${key}=" .env || missing="$missing $key"
    done < <(grep -E '^[A-Z][A-Z0-9_]*=' .env.example.prod | cut -d= -f1)

    if [ -n "$missing" ]; then
        warn ".env is missing keys present in .env.example.prod:$missing"
        warn "Copy them over and re-run."
    fi

    log "Configured for DOMAIN=$DOMAIN"
}
