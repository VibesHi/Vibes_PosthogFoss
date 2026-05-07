#!/usr/bin/env bash
#
# Shared helpers for bin/setup-prod modules.
#
# This file is sourced (not executed). It MUST NOT call `set -euo pipefail`
# itself — the orchestrator (bin/setup-prod) does that once, and we don't want
# to override its choices when other tools source us.
#
# Conventions:
#   - log/warn/die use bracketed prefix [setup-prod] for grep-ability.
#   - SUDO is empty when run as root, "sudo" otherwise. All docker invocations
#     funnel through dc() so a future migration to e.g. `nerdctl compose` is a
#     one-line change.
#   - _source_env() exists because setup-prod re-sources .env after writing
#     derived values; doing it via a helper keeps `set -a` discipline in one place.
#
# Re-source guard: every helper here is idempotent on multiple sources, so the
# orchestrator can `source` this file as many times as it likes. Modules that
# need this file MUST source it explicitly — relying on a parent's prior source
# leaks coupling.

# ---------------------------------------------------------------------------
# Colors / logging
# ---------------------------------------------------------------------------

readonly C_BLUE=$'\033[1;34m'
readonly C_YELLOW=$'\033[1;33m'
readonly C_RED=$'\033[1;31m'
readonly C_RESET=$'\033[0m'

log()  { printf '%s[setup-prod]%s %s\n' "$C_BLUE"   "$C_RESET" "$*"; }
warn() { printf '%s[setup-prod]%s %s\n' "$C_YELLOW" "$C_RESET" "$*" >&2; }
die()  { printf '%s[setup-prod]%s %s\n' "$C_RED"    "$C_RESET" "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Privilege + docker shorthands
# ---------------------------------------------------------------------------

# sudo only when not root. Most docker invocations need it; preserve for
# brotli installs too. Users in the `docker` group + passwordless sudo will
# never see a prompt.
SUDO=""
[ "$(id -u)" -eq 0 ] || SUDO="sudo"

dc()       { $SUDO docker compose "$@"; }
ch_query() { dc exec -T clickhouse clickhouse-client --password="$CLICKHOUSE_PASSWORD" --query "$1"; }

# ---------------------------------------------------------------------------
# .env sourcing
# ---------------------------------------------------------------------------
#
# `set -a` exports every variable assigned during the scope. Multi-token values
# in .env MUST be double-quoted (e.g. CADDY_HOST="a, b, c") or `source` will
# tokenize them and fail with "command not found" on the trailing tokens.
# docker-compose strips the outer quotes when reading .env, so the container
# env stays correct.

_source_env() {
    local env_file="${1:-.env}"
    set -a
    # shellcheck disable=SC1090
    source "$env_file"
    set +a
}

# ---------------------------------------------------------------------------
# ClickHouse readiness polling
# ---------------------------------------------------------------------------
#
# Polls a CH SQL predicate until it returns "1" or the timeout hits.
# Args: <description> <timeout_seconds> <progress_interval_seconds> <query>
# The query MUST return exactly "1" on success (e.g. `EXISTS table` or `SELECT 1`).

ch_wait_for() {
    local desc="$1" timeout="$2" interval="$3" query="$4"
    local elapsed=0 result
    log "Waiting for $desc (up to ${timeout}s)"
    while [ "$elapsed" -lt "$timeout" ]; do
        # On any failure (container down, auth fail, table missing) clickhouse-client
        # writes to stderr (suppressed) and we get an empty / non-"1" result.
        # `|| true` keeps the loop alive while we retry.
        result=$( { ch_query "$query" 2>/dev/null || true; } | tr -d '[:space:]')
        if [ "$result" = "1" ]; then
            log "  -> $desc ready"
            return 0
        fi
        sleep 5
        elapsed=$((elapsed + 5))
        if [ $((elapsed % interval)) -eq 0 ]; then
            log "  -> still waiting on $desc (${elapsed}s elapsed)"
        fi
    done
    return 1
}

# ---------------------------------------------------------------------------
# brotli auto-install (used by the GeoIP phase)
# ---------------------------------------------------------------------------
#
# Best-effort install across common package managers. Hard error if none is
# present so the user knows to install brotli manually.

install_brotli() {
    if command -v brotli >/dev/null 2>&1; then return 0; fi
    if   command -v apt-get >/dev/null 2>&1; then log "Installing brotli via apt-get"; $SUDO apt-get install -y --no-install-recommends brotli
    elif command -v dnf     >/dev/null 2>&1; then log "Installing brotli via dnf";     $SUDO dnf install -y brotli
    elif command -v yum     >/dev/null 2>&1; then log "Installing brotli via yum";     $SUDO yum install -y brotli
    elif command -v pacman  >/dev/null 2>&1; then log "Installing brotli via pacman";  $SUDO pacman -S --noconfirm brotli
    else die "brotli not found and no known package manager. Install brotli manually and re-run."
    fi
}
