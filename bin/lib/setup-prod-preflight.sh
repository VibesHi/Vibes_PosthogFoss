#!/usr/bin/env bash
#
# Phase 2: host pre-flight (docker, RAM, swap, disk).
# Phase 2b: ee/ stub package integrity.

# ---------------------------------------------------------------------------
# Phase 2: pre-flight
# ---------------------------------------------------------------------------

phase_preflight() {
    log "Running pre-flight checks"

    command -v docker >/dev/null 2>&1 \
        || die "docker not found. Install: https://docs.docker.com/engine/install/"
    $SUDO docker info >/dev/null 2>&1 \
        || die "docker daemon not reachable. Try 'systemctl start docker' or join the 'docker' group."
    dc version >/dev/null 2>&1 \
        || die "docker compose v2 plugin not found. Install docker-compose-plugin or upgrade docker."

    # Memory: prod compose has 32 services with ~30 GB of mem_limit budget.
    # Below 16 GB the stack will OOM-cascade as soon as ClickHouse warms up.
    # 8 GB is enough only with most ingestion services disabled.
    local mem_gb=0
    if [ -f /proc/meminfo ]; then
        mem_gb=$(awk '/^MemTotal:/ {print int($2/1024/1024)}' /proc/meminfo)
    elif command -v sysctl >/dev/null 2>&1; then
        mem_gb=$(( $(sysctl -n hw.memsize 2>/dev/null || echo 0) / 1073741824 ))
    fi
    if   [ "$mem_gb" -eq 0 ];   then warn "Could not detect RAM. PostHog prod needs 16 GB+ for the full stack (or trim mem_limits in docker-compose.prod.yml)."
    elif [ "$mem_gb" -lt 16 ];  then warn "Detected ${mem_gb} GB RAM. PostHog prod needs 16 GB+ as configured. Either upgrade the host or trim mem_limits."
    fi

    # Swap: prod stack runs tight on a 30 GB box; 4 GB swap is panic-valve
    # against transient spikes. setup-prod doesn't create it because that
    # touches /etc/fstab + kernel state, and many container/VPS hosts (LXC,
    # restricted Docker hosts) forbid user-controlled swap. Use bin/setup-swap
    # explicitly when the host allows it.
    local swap_gb=0
    if [ -f /proc/meminfo ]; then
        swap_gb=$(awk '/^SwapTotal:/ {print int($2/1024/1024)}' /proc/meminfo)
    fi
    if [ "$swap_gb" -lt 2 ]; then
        warn "Swap is ${swap_gb} GB (recommend 4 GB). Without swap, transient memory spikes will OOM-kill the largest container instead of paging out."
        warn "Create with:  sudo bin/setup-swap        # idempotent, validates host support"
    fi

    # Disk: prod build + ClickHouse data + replay blobs + container logs.
    # Build itself is 30-50 GB transient. Threshold matches the warning text.
    # `df -BG` is a GNU-ism; on macOS this returns 0 and we silently skip.
    local avail_gb
    avail_gb=$(df -BG . 2>/dev/null | awk 'NR==2 {gsub(/G/,"",$4); print int($4)}' || echo 0)
    local min_gb=150
    if [ "${avail_gb:-0}" -gt 0 ] && [ "$avail_gb" -lt "$min_gb" ]; then
        warn "Only ${avail_gb} GB disk available. Recommend ${min_gb} GB+ for build (30-50 GB transient) + event data + replay."
        warn "Reclaim with:  docker system prune -a -f --volumes"
    fi

    log "Pre-flight OK (RAM=${mem_gb} GB, swap=${swap_gb} GB, disk=${avail_gb:-?} GB)"
}

# ---------------------------------------------------------------------------
# Phase 2b: ee/ stub integrity
# ---------------------------------------------------------------------------
#
# This fork ships ee/ as a stub package. Two failure modes the build always
# hits when stubs go out of sync with the codebase:
#
#   1. ee/ directory was removed (e.g. an upstream sync auto-strip ran).
#      Build fails at `COPY ee ee/` and then again at every `from ee.*` import.
#   2. New `from ee.X import Y` was added in upstream code but the matching
#      stub doesn't exist yet. Django boot fails with ModuleNotFoundError or
#      ImportError on the missing symbol.
#
# We delegate the actual scan to bin/check-ee-stub-symbols (Python AST walk).
# That checker covers both failure modes (missing files AND missing symbols)
# strictly more accurately than a bash regex pre-pass — the previous bash
# version of this phase has been removed.

phase_check_ee_stubs() {
    log "Checking ee/ stub package integrity"

    if [ ! -d "ee" ]; then
        warn "ee/ directory missing. The Docker build will fail at 'COPY ee ee/'."
        warn "Restore stubs with: git checkout HEAD -- ee/   (if previously committed)"
        warn "Or restore upstream EE: git fetch upstream && git checkout upstream/master -- ee/"
        return 0  # soft-fail: let the user proceed if they know what they're doing
    fi

    [ -f "ee/__init__.py" ] || warn "ee/__init__.py missing — ee is not a Python package."
    [ -f "ee/apps.py" ]     || warn "ee/apps.py missing — Django won't register the ee app."

    # AST-based scan. Surfaces missing files AND missing symbols within an
    # existing stub (e.g. `from ee.X.Y import Z` where ee/X/Y.py exists but
    # doesn't define Z) before we burn 4+ minutes on a docker build that's
    # going to crash at `manage.py collectstatic`.
    if [ -x "bin/check-ee-stub-symbols" ] && command -v python3 >/dev/null 2>&1; then
        if python3 bin/check-ee-stub-symbols >&2; then
            log "ee/ stub coverage looks complete"
        else
            warn "Add the missing symbols and rerun. (continuing — runtime imports will fail)"
        fi
    else
        warn "bin/check-ee-stub-symbols not found or python3 missing — skipping stub symbol check."
    fi
}
