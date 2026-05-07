#!/usr/bin/env bash
#
# Phase 7: async migrations check.

# ---------------------------------------------------------------------------
# is_existing_install
# ---------------------------------------------------------------------------
#
# An "existing install" is one where Django migrations have run at least
# once. We can't just check for the postgres-data volume — a previous
# setup-prod that crashed before web could finish bin/migrate (e.g. kafka
# OOM, ClickHouse misconfig) leaves an empty PG data dir behind, and
# asyncmigrationscheck later dies with `UndefinedTable: posthog_asyncmigration`
# because Django boot calls setup_async_migrations() which queries that
# table during AppConfig.ready().
#
# Canonical marker: presence of `django_migrations` in the public schema.
# It's created by the very first `manage.py migrate` and never dropped.

is_existing_install() {
    local project="${COMPOSE_PROJECT_NAME:-posthog}"

    $SUDO docker volume ls --format '{{.Name}}' \
        | grep -qE "^${project}_postgres-data$" || return 1

    # Volume exists. Bring up just db so we can query it. Idempotent — no-op
    # if it's already running. db has no depends_on, so this won't cascade
    # into starting the rest of the stack.
    dc up -d db >/dev/null 2>&1 || {
        warn "Couldn't start db to verify migration state — treating as fresh install."
        return 1
    }

    # Wait up to 60s for PG to accept connections. Warm volumes start in <10s.
    local elapsed=0
    until dc exec -T db pg_isready -U posthog -d posthog >/dev/null 2>&1; do
        sleep 2
        elapsed=$((elapsed + 2))
        if [ "$elapsed" -ge 60 ]; then
            warn "db didn't accept connections in 60s — treating as fresh install."
            return 1
        fi
    done

    if dc exec -T db psql -U posthog -d posthog -tAc \
        "SELECT 1 FROM information_schema.tables \
         WHERE table_schema='public' AND table_name='django_migrations'" \
        2>/dev/null | grep -q '^1$'; then
        return 0
    fi

    warn "Postgres volume exists but django_migrations table is missing —"
    warn "previous install likely crashed before web ran bin/migrate."
    warn "Treating as fresh install; web will run migrations on next start."
    return 1
}

# ---------------------------------------------------------------------------
# Phase 7: async migrations check
# ---------------------------------------------------------------------------
#
# Two-step process — see the long-form rationale in the original script
# (this function preserves the same control flow):
#
#   1. Mark no-op async migrations complete via a worker run with
#      SKIP_ASYNC_MIGRATIONS_SETUP=1 (works around apps.py:ready() crashing
#      during AppConfig.ready() on fresh-but-warm-volume installs).
#   2. Run the dedicated asyncmigrationscheck container with the strict
#      SKIP_ASYNC_MIGRATIONS_SETUP=0 default.

phase_async_migrations_check() {
    if ! is_existing_install; then
        log "Fresh install — skipping async migrations check"
        return 0
    fi

    # Step 1: mark no-op async migrations complete.
    #
    # Background: `posthog/apps.py::ready()` calls `setup_async_migrations()`
    # at Django boot. With `SKIP_ASYNC_MIGRATIONS_SETUP=0` the setup will
    # raise ImproperlyConfigured for ANY async migration that is unapplied
    # AND has `posthog_max_version` < `FROZEN_POSTHOG_VERSION` -- no
    # `is_required()` check. On a fresh install, several migrations (e.g.
    # `0004_replicated_schema`) are no-ops because we already create the
    # tables in their post-state, but they sit in the AsyncMigration table
    # as `not_started` and the boot crashes.
    #
    # `bin/migrate` solves this on first web boot by running
    # `--complete-noop-migrations` BEFORE `--check`, but bin/migrate runs
    # inside the web container at first start. setup-prod's
    # `phase_async_migrations_check` runs in a separate `dc run --rm
    # asyncmigrationscheck` container that has SKIP_ASYNC_MIGRATIONS_SETUP=0
    # and crashes during AppConfig.ready() before its --check argument is
    # ever consulted.
    #
    # Workaround: reuse the worker image (same code) but force
    # SKIP_ASYNC_MIGRATIONS_SETUP=1 so apps.py:ready() skips the strict
    # version gate, then invoke `run_async_migrations
    # --complete-noop-migrations` which calls setup_async_migrations(
    # ignore_posthog_version=True) internally and marks no-op migrations
    # complete. After this, the regular asyncmigrationscheck succeeds.
    log "Existing install detected — marking no-op async migrations complete"
    if ! dc run --rm \
        -e SKIP_ASYNC_MIGRATIONS_SETUP=1 \
        worker python manage.py run_async_migrations --complete-noop-migrations; then
        warn "Could not mark no-op async migrations complete — async check may fail"
    fi

    # Step 2: strict check via the dedicated container with
    # SKIP_ASYNC_MIGRATIONS_SETUP=0 (its compose default). Brings up
    # dependencies automatically via the worker chain in
    # docker-compose.base.yml. Non-zero exit means migrations are pending or
    # failed their dry-run.
    log "Running async migrations check"
    if ! dc run --rm asyncmigrationscheck; then
        die "Pending async migrations. See https://posthog.com/docs/runbook/async-migrations and resolve before continuing."
    fi
    log "Async migrations check passed"
}
