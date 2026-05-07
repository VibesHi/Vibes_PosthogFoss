#!/usr/bin/env bash
#
# Phase 3:  scaffold compose/ entrypoints (web, temporal-worker, wait).
# Phase 3b: mirror proto/ -> rust/proto/ for the rust build context.
# Phase 4:  download GeoIP database.

# ---------------------------------------------------------------------------
# Phase 3: compose/ runtime scripts
# ---------------------------------------------------------------------------

write_if_missing() {
    local path="$1"; shift
    if [ ! -x "$path" ]; then
        log "Creating $path"
        cat > "$path"
        chmod +x "$path"
    fi
}

phase_compose_scripts() {
    mkdir -p compose

    write_if_missing compose/start <<'EOF'
#!/bin/bash
./compose/wait
./bin/migrate
./bin/docker-server
EOF

    write_if_missing compose/temporal-django-worker <<'EOF'
#!/bin/bash
./bin/temporal-django-worker
EOF

    write_if_missing compose/wait <<'EOF'
#!/usr/bin/env python3
import socket, time

def loop():
    print("Waiting for ClickHouse and Postgres to be ready")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect(('clickhouse', 9000))
        print("Clickhouse is ready")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect(('db', 5432))
        print("Postgres is ready")
    except ConnectionRefusedError:
        time.sleep(5)
        loop()

loop()
EOF
}

# ---------------------------------------------------------------------------
# Phase 3b: mirror proto/ into rust/proto/ for the rust build context
# ---------------------------------------------------------------------------
#
# rust/personhog-proto/build.rs and rust/kafka-assigner-proto/build.rs invoke
# tonic-build with PROTO_ROOT=../proto, which inside the container resolves to
# /app/proto. The rust services build with `context: ./rust` (see
# docker-compose.prod.yml), so the Dockerfile's `COPY . .` only ships rust/
# into /app — proto/ at the workspace root is left out, and the build dies
# with `protoc failed: Could not make proto path relative: ../proto/...`.
#
# Cheapest fix: mirror proto/ into rust/proto/ here. The Dockerfile and
# docker-compose stay byte-identical to upstream so future syncs are clean.
# rust/proto/ is .gitignore'd so this stays out of the working tree.
#
# Symlinks would be simpler but Docker COPY refuses to follow symlinks that
# point outside the build context.

phase_sync_proto() {
    [ -d proto ] || die "proto/ missing at workspace root — repo is incomplete."

    log "Mirroring proto/ -> rust/proto/ (build-context shim)"
    if command -v rsync >/dev/null 2>&1; then
        # rsync -a --delete preserves BuildKit layer cache when proto/ is
        # unchanged (only diffs hit disk → COPY layer hash stays stable).
        rsync -a --delete proto/ rust/proto/
    else
        rm -rf rust/proto
        cp -R proto rust/proto
    fi
}

# ---------------------------------------------------------------------------
# Phase 4: GeoIP database
# ---------------------------------------------------------------------------

phase_geoip() {
    mkdir -p share
    if [ -f share/GeoLite2-City.mmdb ]; then
        log "GeoIP database already present (skip)"
        return 0
    fi

    log "Downloading GeoIP database"
    install_brotli
    curl -L https://mmdbcdn.posthog.net/ --http1.1 \
        | brotli --decompress --output=share/GeoLite2-City.mmdb
    printf '{"date": "%s"}\n' "$(date +%Y-%m-%d)" > share/GeoLite2-City.json
    chmod 644 share/GeoLite2-City.mmdb share/GeoLite2-City.json
    log "GeoIP database ready"
}
