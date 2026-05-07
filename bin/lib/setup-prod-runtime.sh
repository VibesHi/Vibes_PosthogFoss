#!/usr/bin/env bash
#
# Phase 8:  start the stack (with retry).
# Phase 9:  wait for ClickHouse auth.
# Phase 10: apply runtime SQL TTLs (idempotent, best-effort).
# Phase 11: backup config soft-warn (non-blocking).

# ---------------------------------------------------------------------------
# Phase 8: start the stack
# ---------------------------------------------------------------------------

phase_compose_up() {
    local max_attempts=3 attempt
    for attempt in $(seq 1 $max_attempts); do
        log "Starting stack (attempt $attempt/$max_attempts)"
        if dc up -d; then
            phase_verify_replicas
            return 0
        fi
        if [ "$attempt" -lt "$max_attempts" ]; then
            warn "compose up failed — retrying in 30s"
            sleep 30
        fi
    done
    die "Failed to start stack after $max_attempts attempts. Check 'docker compose logs' for details."
}

# Sanity-check that `deploy.replicas: N` was honored. Compose v2.10+ honors
# replicas in non-Swarm `up`, but older versions (<2.10) silently start one
# replica regardless. Catches a lurking config bug if someone backports this
# stack to an older Docker Engine.
#
# Replica counts must match docker-compose.prod.yml `deploy.replicas` blocks.
# Update both together when scaling for higher RPS.
phase_verify_replicas() {
    local svc expected actual
    declare -A expected_replicas=(
        [ingestion-general]=4
        [capture]=3
    )
    for svc in "${!expected_replicas[@]}"; do
        expected="${expected_replicas[$svc]}"
        actual=$(dc ps --status running --format '{{.Service}}' 2>/dev/null \
            | grep -c "^${svc}\$" || true)
        if [ "$actual" -lt "$expected" ]; then
            warn "Expected $expected '${svc}' replicas, got $actual."
            warn "Check: docker compose version (need v2.10+) and 'docker compose ps ${svc}'"
            warn "Workaround: add '--scale ${svc}=${expected}' to compose up if the deploy.replicas key isn't honored."
        else
            log "Replica check: ${svc}=$actual (expected $expected) ✓"
        fi
    done
}

# ---------------------------------------------------------------------------
# Phase 9: wait for ClickHouse
# ---------------------------------------------------------------------------
# Two-stage wait:
#   a. CH server reachable + accepts the rotated password (5min)
#   b. (per-table, in phase 10) target table created by Django migrations (10min)
#
# Stage (a) failing means something's wrong with the CH config — bail out of
# TTL application but don't die, so the user can fix and re-run.

phase_wait_clickhouse_ready() {
    if ch_wait_for "ClickHouse auth" 300 30 "SELECT 1"; then
        return 0
    fi
    warn "ClickHouse didn't accept the rotated password in 5min."
    warn "Likely causes: clickhouse container crash-looping, CLICKHOUSE_PASSWORD mismatch, or stale data dir from a previous install with a different password."
    warn "Inspect with: docker compose logs clickhouse"
    return 1
}

# ---------------------------------------------------------------------------
# Phase 10: apply runtime SQL TTLs (idempotent, best-effort)
# ---------------------------------------------------------------------------
# Re-applies on every run so values self-heal if a future PostHog migration
# overwrites them. Each call returns non-zero on failure; we count failures
# and reflect them in the final summary instead of dying.

apply_ttl() {
    local table="$1" time_col="$2" days="$3"
    log "TTL: $table = $days days"

    if ! ch_wait_for "table $table" 600 60 "EXISTS $table"; then
        warn "  $table not present after 10min. Migrations may still be running."
        warn "  Tail 'docker compose logs -f web' to monitor; re-run setup-prod once it's done."
        return 1
    fi

    if ch_query "
        ALTER TABLE $table
            MODIFY TTL toDate($time_col) + INTERVAL $days DAY DELETE
            SETTINGS materialize_ttl_after_modify = 1
    "; then
        log "  -> applied"
        return 0
    fi
    warn "  -> ALTER failed (see error above)"
    return 1
}

phase_apply_ttls() {
    local failures=0
    apply_ttl "posthog.sharded_log_entries"        "timestamp"  "${POSTHOG_LOG_ENTRIES_TTL_DAYS:-14}"        || failures=$((failures + 1))
    apply_ttl "posthog.sharded_query_log_archive"  "event_date" "${POSTHOG_QUERY_LOG_ARCHIVE_TTL_DAYS:-30}" || failures=$((failures + 1))
    return "$failures"
}

# ---------------------------------------------------------------------------
# Phase 11: Backup config soft-warn (non-blocking)
# ---------------------------------------------------------------------------
#
# The `backup` profile (docker-compose.prod.yml) ships off-host backups to
# GCS. It's opt-in because it requires real GCS credentials (HMAC keys +
# bucket) that we can't auto-provision. Three failure modes worth catching
# at setup-time rather than discovering 90 days later when you actually
# need to restore:
#
#   1. Profile not enabled → no backups happening at all.
#   2. Profile enabled but GCS_* vars missing/empty → container crash-loops.
#   3. Profile enabled + GCS_* set but lifecycle.json never applied →
#      bucket grows unbounded (no cost ceiling, deleted backups never
#      expire). We can detect (1) and (2) here; (3) requires a live GCS
#      call which needs the user's gcloud auth, so we just remind.
#
# Non-blocking by design: backups are critical but configuring them is the
# operator's call, not a setup-prod responsibility.

phase_check_backup_config() {
    local profiles="${COMPOSE_PROFILES:-}"
    if [[ ",${profiles}," != *",backup,"* ]]; then
        warn ""
        warn "==================================================================="
        warn "BACKUPS NOT CONFIGURED. You currently have NO off-host disaster"
        warn "recovery. Enable with:"
        warn "  1. Edit .env, set GCS_BUCKET / GCS_HMAC_KEY / GCS_HMAC_SECRET"
        warn "  2. Add 'backup' to COMPOSE_PROFILES (e.g. COMPOSE_PROFILES=backup)"
        warn "  3. docker compose up -d --build backup"
        warn "  4. Apply bucket lifecycle: see README.prod.md '## Backups'"
        warn "==================================================================="
        return 0
    fi

    local missing="" var
    for var in GCS_BUCKET GCS_HMAC_KEY GCS_HMAC_SECRET; do
        if [ -z "${!var:-}" ]; then
            missing="$missing $var"
        fi
    done
    if [ -n "$missing" ]; then
        warn "backup profile enabled but .env is missing:${missing}"
        warn "Fill these in or remove 'backup' from COMPOSE_PROFILES."
        return 0
    fi

    log "Backup profile enabled and GCS_* vars present. Reminder: ensure the"
    log "bucket lifecycle policy from docker/backup/lifecycle.json has been"
    log "applied at least once (see README.prod.md '## Backups')."
}
