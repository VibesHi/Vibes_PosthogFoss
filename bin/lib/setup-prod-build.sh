#!/usr/bin/env bash
#
# Phase 5: pull third-party images.
# Phase 6: build local images.

# ---------------------------------------------------------------------------
# Phase 5: pull
# ---------------------------------------------------------------------------
#
# Pull upstream images (postgres, redpanda, redis, zookeeper, caddy, minio,
# seaweedfs, temporalio/*, elasticsearch) upfront. Surfaces network errors
# before any container starts and keeps `up` snappy.
#
# `--ignore-pull-failures` lets us skip locally-built services without
# failing the whole step (compose tries to pull `posthog-app` etc. and 404s —
# that's expected).

phase_pull() {
    log "Pulling third-party images"
    dc pull --ignore-pull-failures || warn "Some images failed to pull — continuing (build may still succeed if you have them cached)"
}

# ---------------------------------------------------------------------------
# Phase 6: build
# ---------------------------------------------------------------------------

phase_build() {
    log "Building local images (20-40 min on first run, near-instant on re-run)"
    dc build
}
