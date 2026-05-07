# Self-Hosted PostHog (production-ish)

Run PostHog from your forked repo with locally-built images, no upstream
registry, no separate installer binary. Optimized for a single-host VPS
deployment that you can patch and redeploy from `git`.

## Files

### Boot / runtime

| Path                          | Purpose                                                 |
|-------------------------------|---------------------------------------------------------|
| `docker-compose.prod.yml`     | Production compose file (forked from `docker-compose.hobby.yml`) |
| `bin/setup-prod`              | Idempotent per-host bootstrap (orchestrator, ~70 lines) |
| `bin/lib/setup-prod-*.sh`     | Phase implementations sourced by the orchestrator       |
| `.env.example.prod`           | Template for `.env` (manual, NOT generated)             |
| `compose/start`               | Web entrypoint (created by `setup-prod`)                |
| `compose/temporal-django-worker` | Temporal worker entrypoint (created by `setup-prod`) |
| `compose/wait`                | TCP wait-for-deps script (created by `setup-prod`)      |
| `share/GeoLite2-City.mmdb`    | GeoIP database (downloaded by `setup-prod`)             |

### Operations (gated by `COMPOSE_PROFILES`)

| Path                                            | Purpose                                                                                                  |
|-------------------------------------------------|----------------------------------------------------------------------------------------------------------|
| [`docker/backup/`](docker/backup/README.md)     | DR backups — daily PG dump + CH partitions to GCS, profile `backup`. See [Backups](#backups).            |
| [`docker/posthog-events-export/`](docker/posthog-events-export/README.md) | Daily Mixpanel-shape JSONL.gz event archive to GCS, profile `events-export`. See [Events export](#events-export). |
| [`ee/scripts/posthog_events_export/`](ee/scripts/posthog_events_export/README.md) | Python script behind `events-export` (schema mapping, CLI flags, manual backfills).                      |

### Migration tooling (one-shot, no profile)

Used during Mixpanel → PostHog historical import. Not part of steady-state ops. See [Mixpanel migration](#mixpanel-migration).

| Path                                                                  | Purpose                                                                                |
|-----------------------------------------------------------------------|----------------------------------------------------------------------------------------|
| [`ee/scripts/mixpanel_export/`](ee/scripts/mixpanel_export/README.md) | Pulls historical events FROM the Mixpanel Export API to GCS as monthly `.jsonl.gz`.    |
| [`ee/scripts/mixpanel_splitter/`](ee/scripts/mixpanel_splitter/README.md) | Cloud Run Job that splits Mixpanel monthly files into per-day `.jsonl.gz`.             |
| [`ee/scripts/mixpanel_import/RUNBOOK.md`](ee/scripts/mixpanel_import/RUNBOOK.md) | End-to-end runbook: GCS → `batch-import-worker` → Kafka → ClickHouse.                  |

## First-run

```bash
# 1. Copy the env template and fill it in
cp .env.example.prod .env
$EDITOR .env

# 2. Bootstrap (validates .env, scaffolds compose/, downloads GeoIP, builds, starts)
bin/setup-prod
```

### Quick `.env` setup

```bash
cp .env.example.prod .env

# DOMAIN appears twice (DOMAIN= and inside CADDY_HOST=)
DOMAIN_NEW=posthog.example.com
sed -i.bak "s|CHANGE_ME_your.domain.tld|${DOMAIN_NEW}|g" .env

# POSTHOG_SECRET (Django SECRET_KEY) -- 64 hex chars
sed -i.bak "0,/CHANGE_ME_openssl_rand_hex_32/s||$(openssl rand -hex 32)|" .env

# ENCRYPTION_SALT_KEYS (Fernet input) -- MUST be 32 chars, hence hex 16
sed -i.bak "0,/CHANGE_ME_openssl_rand_hex_16/s||$(openssl rand -hex 16)|" .env

# Three independent passwords -- 0,/.../ replaces only the first match each time
for _ in 1 2 3; do
    sed -i.bak "0,/CHANGE_ME_openssl_rand_hex_24/s||$(openssl rand -hex 24)|" .env
done

rm .env.bak
bin/setup-prod
```

## Subsequent deploys (after code changes)

```bash
git pull
bin/setup-prod          # rebuilds only changed layers, restarts changed services
```

For a hard restart of a single service:

```bash
docker compose up -d --build --force-recreate web
```

> **Note:** `COMPOSE_PROJECT_NAME=posthog` and `COMPOSE_FILE=docker-compose.prod.yml`
> are set in `.env`, so plain `docker compose ...` works from the repo root.
> Prefix with `sudo` if you're not root and not in the `docker` group.

## What `bin/setup-prod` does

1. **Validates `.env`** — fails fast if missing, if any `CHANGE_ME_*` sentinel
   remains, or if any of `DOMAIN`, `POSTHOG_SECRET`, `ENCRYPTION_SALT_KEYS`,
   `POSTHOG_DB_PASSWORD`, `OBJECT_STORAGE_PASSWORD`, `CLICKHOUSE_PASSWORD` is
   empty. Also warns if any password is shorter than 16 characters, and if
   `.env` is missing keys that exist in `.env.example.prod` (schema drift after
   `git pull`).
2. **Pre-flight checks** — verifies `docker` + `docker compose` v2 are
   installed and the daemon is reachable; warns if RAM &lt; 16 GB, swap &lt; 2 GB,
   or disk free &lt; 150 GB. Thresholds match the actual mem_limit budget
   (~30 GB sum across services + ~25 GB transient build cost).
3. **Generates `compose/start`, `compose/temporal-django-worker`, `compose/wait`**
   — only if they don't already exist (so your edits survive re-runs).
4. **Downloads `share/GeoLite2-City.mmdb`** — only if missing. Auto-installs
   `brotli` via `apt-get` / `dnf` / `yum` / `pacman` (whichever is present).
5. **Pulls third-party images** upfront (`docker compose pull
   --ignore-pull-failures`). Surfaces network errors before any container
   starts; locally-built services are silently skipped.
6. **Builds local images** via `docker compose build`.
7. **Async migrations check** — on existing installs (when `posthog_postgres-data`
   or `posthog_clickhouse-data` volumes already exist), runs
   `docker compose run --rm asyncmigrationscheck` and aborts if any are
   pending. Skipped on fresh installs.
8. **Starts the stack** via `docker compose up -d`, retrying up to 3× with 30s
   backoff on failure.
9. **Waits for ClickHouse readiness** — polls `clickhouse-client --password`
   for up to 5 min until the rotated CH password is accepted. If the timeout
   hits, exits with a warning rather than failing the whole run.
10. **Applies idempotent ClickHouse TTLs** — waits up to 10 min for
    `sharded_log_entries` / `sharded_query_log_archive` to be created by Django
    migrations, then `MODIFY TTL` on each. Configurable via
    `POSTHOG_LOG_ENTRIES_TTL_DAYS` / `POSTHOG_QUERY_LOG_ARCHIVE_TTL_DAYS`.
    Re-running self-heals if a future PostHog migration ever resets the TTL.

The script does **not** generate `.env` — fully manual.

## Usage notes

- **`COMPOSE_FILE` is locked to `docker-compose.prod.yml` via `.env`.** Don't
  manually add `docker-compose.base.yml` to it — that drags in dev-only services
  (`kafka_ui`, `flower`, `opensearch`, `localstack`, `maildev`, `otel-collector`,
  `jaeger`, `duckgres`, `capture-ai`, `capture-logs`). The `extends:` directives
  inside prod.yml already pull from base.yml.
- **Don't run `docker compose pull`.** All custom services have local-only
  image names (`posthog-app`, `posthog-node`, `posthog-<rust-bin>`); a `pull`
  would error on missing remote tags. Only third-party images
  (`postgres`, redpanda for `kafka-init`) are pullable.
- **First build is slow** (~20-40 min) — Python deps, frontend bundle, Rust
  compilation. Subsequent builds are minutes thanks to BuildKit cache.

## Logs and ops

```bash
docker compose ps
docker compose logs -f web worker
docker compose restart plugins
docker compose down                  # stop all
docker compose down -v               # stop AND wipe volumes (DESTRUCTIVE)
```

---

## What we changed in `docker-compose.prod.yml` vs upstream `docker-compose.hobby.yml`

The prod file is a fork of `docker-compose.hobby.yml` with three categories of changes.

### 1. Path corrections — running from the repo root

The hobby installer clones the repo into `./posthog/`, so its compose file
references `./posthog/posthog/idl`, `./posthog/docker/clickhouse/...`, etc.
We run from the repo root (where `docker-compose.prod.yml` itself lives), so
those `./posthog/` prefixes are wrong.

| Service              | Before (`hobby.yml`)                                | After (`prod.yml`)                          |
|----------------------|-----------------------------------------------------|---------------------------------------------|
| `clickhouse` volumes | `./posthog/posthog/idl:/idl`                        | `./posthog/idl:/idl`                        |
|                      | `./posthog/docker/clickhouse/...`                   | `./docker/clickhouse/...`                   |
|                      | `./posthog/posthog/user_scripts:...`                | `./posthog/user_scripts:...`                |
| `temporal` volumes   | `./posthog/docker/temporal/dynamicconfig:...`       | `./docker/temporal/dynamicconfig:...`       |
| `livestream` volumes | `./posthog/docker/livestream/configs-hobby.yml:...` | `./docker/livestream/configs-hobby.yml:...` |
| Rust services build  | `context: ./posthog/rust`                           | `context: ./rust`                           |

Total: 14 path fixes.

### 2. Local builds instead of registry pulls

Hobby pulls images from `posthog/posthog:$POSTHOG_APP_TAG` (Docker Hub) and
`posthog/posthog-node:$POSTHOG_NODE_TAG`. We build from source in this repo.

Added `build:` directives:

| Services                                                                    | Build directive                          | Image tag           |
|-----------------------------------------------------------------------------|------------------------------------------|---------------------|
| `web`, `worker`, `asyncmigrationscheck`, `temporal-django-worker`           | `build: .`                               | `posthog-app`       |
| `plugins`, `ingestion-general`, `ingestion-sessionreplay`, `recording-api`, `ingestion-error-tracking`, `ingestion-logs`, `ingestion-traces` | `build: { context: ., dockerfile: Dockerfile.node }` | `posthog-node` |
| `cyclotron-janitor`, `capture`, `replay-capture`, `property-defs-rs`, `feature-flags`, `hypercache-server`, `cymbal` | `build: { context: ./rust }` (with `BIN` arg from base.yml) | `posthog-<bin>` |
| `livestream`                                                                | `build: { context: ./livestream }`       | `posthog-livestream` |

The `BIN` build arg for each Rust service (e.g. `BIN: capture`) is inherited
from `docker-compose.base.yml` via `extends:`.

### 3. Image tag overrides

Hobby uses `image: $REGISTRY_URL:$POSTHOG_APP_TAG` (Python) and
`image: ${REGISTRY_URL}-node:${POSTHOG_NODE_TAG:-latest}` (Node). Without a
registry, those tags are meaningless. We replaced them with stable local tags:

```diff
- image: $REGISTRY_URL:$POSTHOG_APP_TAG          # web, worker, etc.
+ image: posthog-app

- image: ${REGISTRY_URL}-node:${POSTHOG_NODE_TAG:-latest}    # plugins, ingestion-*
+ image: posthog-node

- image: ghcr.io/posthog/posthog/cymbal:master   # cymbal
+ image: posthog-cymbal
```

Rust services in upstream hobby inherit `image: ghcr.io/posthog/posthog/<bin>:master`
from `docker-compose.base.yml` via `extends:`. We override that inheritance in
`prod.yml` so a stray `docker compose pull` can't replace your local build with
an upstream one:

```yaml
capture:
    image: posthog-capture          # added in prod.yml — overrides base.yml's ghcr.io tag
    build:
        context: ./rust
    extends:
        file: docker-compose.base.yml
        service: capture
```

### Why share `posthog-app` and `posthog-node` across services?

Compose dedups builds when multiple services share the same `image:` + `build:`
context. Sharing one tag for the 4 Python services (which all use `Dockerfile`
at the repo root) means **one build, four containers**. Same for the 7 Node
services sharing `Dockerfile.node`. Net result:

- 1× Python build (`posthog-app`)
- 1× Node build (`posthog-node`)
- 7× Rust builds (one per binary, since each has a different `BIN` arg)
- 1× Go build (`posthog-livestream`)

Total: 10 builds for ~19 services.

### 4. Hardening fixes vs hobby (NOT in upstream)

These address known issues that surface in real production within weeks/months
on a default hobby install.

| Fix | Where | Reason |
|---|---|---|
| `ZOO_AUTOPURGE_PURGEINTERVAL=24` + `ZOO_AUTOPURGE_SNAPRETAINCOUNT=3` on `zookeeper` | prod.yml | Without these, ZK transaction logs grow unbounded — can reach hundreds of GB on busy installs in a few weeks. |
| `objectstorage` ports rebound to `127.0.0.1:` | prod.yml | Hobby exposes MinIO admin console + API on `0.0.0.0:19000-19001` with default `object_storage_root_user`/`password`. Caddy already proxies the public `/posthog/*` path internally — no need for direct port exposure. |
| `temporal` + `temporal-ui` ports rebound to `127.0.0.1:` | prod.yml | Hobby exposes Temporal gRPC (`:7233`) and Web UI (`:8081`) on `0.0.0.0` with no auth. Anyone with the host IP can `tctl` workflow histories, cancel jobs, or start new ones. Tunnel via SSH when you need to debug. |
| `<<: *restart-prod` (`unless-stopped`) on all long-running services | prod.yml | Base.yml uses `restart: on-failure`, which does NOT restart on clean exit (code 0). Some ingestion services exit cleanly under specific conditions and stay down without this. |
| `kafka-init` enhanced with `rpk cluster config set log_retention_ms` + topic-level `alter-config` | prod.yml | base.yml's `--mode dev-container` silently ignores broker-level retention env vars. Without cluster + topic-level overrides, Redpanda disk usage grows linearly until full. |
| ClickHouse `system_log` TTLs via `docker/clickhouse/config.d.prod/system_log_ttl.xml` | new file, mounted in prod.yml | Without TTLs, `query_log` / `trace_log` / `metric_log` / `part_log` grow unbounded — tens of GB in a few weeks on a busy install. Now 7d retention, hardcoded in the XML overlay (CH config layer doesn't interpolate `${ENV_VARS}` from compose). To change, edit the XML and `docker compose restart clickhouse`. There is intentionally **no** `CLICKHOUSE_SYSTEM_LOG_TTL_DAYS` env var — the previous one was dead config. |
| ClickHouse memory caps via `docker/clickhouse/config.d.prod/memory_limits.xml` | new file, mounted in prod.yml | Upstream `config.xml` sets `max_server_memory_usage_to_ram_ratio=0.9`. On a 30 GB box that means CH eats 27 GB and OOM-kills the rest of the stack under any load. Overlay caps to 8 GB hard limit + tightens `max_thread_pool_size` from 10000 (sized for 64-core servers) to 1000. |
| Per-service `mem_limit` via YAML anchors | prod.yml top-of-file | Without these, a single runaway container takes the whole box. Sized for 30 GB / 8 cores. Sum of caps (~35 GB) intentionally overcommits — caps are spike absorbers, not reservations. 4 GB host swap (see `bin/setup-swap`) backstops simultaneous peaks. |
| Redis raised from 200 MB to 1 GB + `volatile-lru` policy | prod.yml | Base.yml's 200 MB / `allkeys-lru` is too small for an instance that's also Celery broker + result backend + hypercache + flag cache + session-replay state. `allkeys-lru` evicts in-flight Celery messages under pressure; `volatile-lru` only evicts keys with TTL (caches), so queues survive. |
| Postgres tuning via `command:` overrides (`shared_buffers=1GB` etc.) | prod.yml + `.env` | Image defaults (`shared_buffers=128MB`) are sized for embedded use. PG gets a 4 GB cgroup (with 5 GB swap headroom for transient `work_mem × max_connections` spikes — worst case ~5.5 GB at 200 conns × 16 MB sort/hash). Tunable via `POSTGRES_*` in `.env` without rebuilding. Lower `POSTGRES_MAX_CONNECTIONS` to 120 if you also want to lower the cgroup cap. |
| Postgres write-throughput tuning (`synchronous_commit=off`, `max_wal_size=4GB`, autovacuum) | prod.yml + `.env` | Default fsync-on-commit + 1GB WAL + 5min checkpoints stalls writers under sustained ingest. New defaults give 3-5x write throughput at the cost of losing the last <200ms of unflushed transactions on a crash — acceptable for analytics (events replay from Kafka's 6h retention). NOT acceptable if you store anything you can't re-derive: flip `POSTGRES_SYNCHRONOUS_COMMIT=on` in `.env`. |
| `ingestion-general` runs with `replicas: 4` | prod.yml | Single Node consumer caps around 3-5k msgs/s (person-resolution + Postgres + ClickHouse writes). Four replicas = ~12-20k sustained. Same-user ordering survives because Rust capture partitions Kafka by `{token}:{distinct_id}` — every event for one user always lands on the same partition, so N consumers in the same group safely parallelize as long as Kafka has ≥N partitions. |
| `capture` runs with `replicas: 3` | prod.yml | Single Rust capture process is bounded by one CPU core (~5-10k req/s). Three replicas use ~3 cores, leaving room for kafka, ingestion-general, and ClickHouse query cores on an 8-core box. Caddy round-robins across them via compose DNS. Bump to 4-6 if loadtest shows capture CPU >80% across all replicas. |
| `events_plugin_ingestion` + `_overflow` + `_historical` pre-created with 4 partitions | prod.yml `kafka-init` | Redpanda auto-creates topics with 1 partition by default; with 1 partition, 3 of the 4 `ingestion-general` replicas sit idle. 4 partitions = 1 partition per replica under cooperative-sticky assignment. `_historical` is included so `batch-import-worker` (Mixpanel/Amplitude backfills) gets the same parallelism instead of bottlenecking through 1 auto-created partition. Tunable via `KAFKA_INGESTION_PARTITIONS` (kafka-init only ever GROWS partitions, never shrinks). |
| Kafka retention raised from 1 h → 6 h | prod.yml | 1 h is enough for normal operation but a 1 h overnight outage drops events. 6 h is the cheapest "I can sleep through a consumer crash" buffer. |
| `CLICKHOUSE_SERVER_IMAGE` pinning via `.env` | prod.yml | Reproducible deploys; prevents silent CH version drift across hosts. |
| `POSTHOG_DB_PASSWORD`, `OBJECT_STORAGE_PASSWORD`, `CLICKHOUSE_PASSWORD` from `.env` | `.env.example.prod`, prod.yml, `docker/clickhouse/users.d.prod/default-password.xml` | Hobby ships with well-known defaults (`posthog`/`posthog`, empty CH `default`, `object_storage_root_password`). Anyone with shell or `docker exec` access on the host can dump data with these. See [Credentials](#credentials) below. |

### Credentials

Three secrets in `.env` swap the well-known defaults baked into upstream
`docker-compose.base.yml` / `docker/clickhouse/users.xml` / `dev-services.env`:

| `.env` variable          | Replaces default                       | Used by                                                                    |
|--------------------------|----------------------------------------|----------------------------------------------------------------------------|
| `POSTHOG_DB_PASSWORD`    | `posthog` (Postgres `posthog` user)    | Django (web/worker), Temporal, all Node ingestion, Rust services           |
| `OBJECT_STORAGE_PASSWORD`| `object_storage_root_password` (MinIO) | `worker`, `web`, `plugins`, `temporal-django-worker` for replay/exports/blobs |
| `CLICKHOUSE_PASSWORD`    | `` (empty CH `default` user password)  | Django + all CH clients; CH server reads it via `from_env` at startup      |

**Implementation:**

- A YAML anchor `&pg-env` at the top of `prod.yml` defines all 9 PG URL flavors
  (`DATABASE_URL`, `PERSONS_DATABASE_URL`, `WRITE_DATABASE_URL`, …, `PERSONS_URL`,
  `PGPASSWORD`, etc.) once. Every service that talks to Postgres merges it via
  `<<: *pg-env` — keeps the password in one place, prevents drift.
- `docker/clickhouse/users.d.prod/default-password.xml` overrides the empty
  `default` user password using `<password from_env="CLICKHOUSE_PASSWORD" />`.
  CH expands `from_env` at config-parse time — same pattern already used by
  `kafka_broker_list` in `docker/clickhouse/config.d/default.xml`.
- Usernames are NOT parameterized. `posthog` (PG), `object_storage_root_user`
  (MinIO), `default` (CH) stay as-is. Renaming them touches PG init scripts,
  MinIO bucket bootstrap, and Django settings — high blast radius for zero
  marginal security gain.
- The `api`/`apppass` and `app`/`apppass` users in `docker/clickhouse/users.xml`
  are unused by any prod service (they're consumed only by `bin/start-worker`,
  `bin/start-celery`, `bin/start-backend` — local dev scripts, not the
  `compose/start` flow). Left untouched.

**Operational consequence:** Direct `clickhouse-client` invocations now need
the password:

```bash
docker compose exec clickhouse clickhouse-client \
    --password="$(grep ^CLICKHOUSE_PASSWORD .env | cut -d= -f2)" \
    --query "SELECT count() FROM posthog.events"
```

Same for `psql`:

```bash
docker compose exec db psql -U posthog -d posthog
# Will prompt for password, or set PGPASSWORD env var first.
```

**Live rotation is not supported.** Changing a password in `.env` after first
boot will desync clients from the running services. Set them once before the
first `bin/setup-prod`. To rotate later: `ALTER USER` in PG, replace MinIO
secret + reset bucket policy, then update `.env` and restart all services in
the right order.

### Things we kept from hobby

- Upstream third-party images (`postgres:15.12-alpine`, redpanda for
  `kafka-init`, `clickhouse/clickhouse-server`, `redis`, `zookeeper`,
  `caddy`, `minio`, `seaweedfs`, `temporalio/*`, `elasticsearch`) — these
  aren't part of PostHog source, no reason to fork.
- All env wiring (`OBJECT_STORAGE_*`, `SESSION_RECORDING_V2_*`, `OTEL_*`, etc.)
- Caddy reverse proxy on `:80`/`:443` with auto-TLS via Let's Encrypt.

---

## FOSS source patches

Files inside `posthog/` (upstream) that this fork modifies. Every patch is
listed here — no undocumented divergence from upstream `posthog/`. When
pulling new upstream master, diff these files first:

```bash
git diff origin/master -- posthog/settings/ee.py posthog/clickhouse/materialized_columns.py
```

Validate with `bin/foss-smoke-test` after every upstream sync — it has
explicit regression checks for these patches.

| File | Patch | Reason |
|---|---|---|
| `posthog/settings/ee.py` | Hardcoded `EE_AVAILABLE = False` (file is fully replaced; full rationale in file header). | Permanent kill switch for `if EE_AVAILABLE:` branches across the codebase. ~20 such branches gate things like RBAC enforcement, Vercel API, scheduled subscriptions, materialized_column_slot viewset, enterprise event/property definitions, SAML SSO. Most have backends in `ee/` that are no-op stubs (return `{}`, return `None`, or raise 501) — flipping `EE_AVAILABLE` True would activate them and produce inconsistent / broken behavior. |
| `posthog/clickhouse/materialized_columns.py` | Modified `else:` branch (when `EE_AVAILABLE=False`): re-imports `get_enabled_materialized_columns` from `ee/` (which is a faithful port of upstream, NOT a stub) and wires `get_materialized_column_for_property` to call it instead of returning `None`. | Two effects: **(1)** unconditional `from posthog.clickhouse.materialized_columns import get_enabled_materialized_columns` in upstream call sites (e.g. `posthog/hogql_queries/web_analytics/events_prefilter.py`) doesn't `ImportError`; **(2)** the HogQL printer's `_get_materialized_column` (`printer/base.py:45`) returns real `MaterializedColumn` objects, so `properties.$xxx` accesses get rewritten to `mat_$xxx` columns — without this patch the printer always emits `JSONExtractRaw(properties, '$xxx')` even when materialized columns exist, causing 5-10× slowdown on Web/Product Analytics queries. Depends on `ee/clickhouse/materialized_columns/columns.py` being a faithful upstream port (commit `e306efc469`), not the previous FOSS no-op stub. |

### Required env vars

`.env.example.prod` lists everything. Required:

- `DOMAIN` — your hostname
- `CADDY_HOST` — Caddy listener spec; must contain `DOMAIN` literally (no `${}` expansion in `.env`). Example: `"posthog.example.com, http://, https://"`
- `POSTHOG_SECRET` — Django secret (`openssl rand -hex 32`)
- `ENCRYPTION_SALT_KEYS` — token encryption (`openssl rand -hex 16`)
- `POSTHOG_DB_PASSWORD` — Postgres `posthog` user (`openssl rand -hex 24`)
- `OBJECT_STORAGE_PASSWORD` — MinIO root (`openssl rand -hex 24`)
- `CLICKHOUSE_PASSWORD` — ClickHouse `default` user (`openssl rand -hex 24`)

Optional:

- `CADDY_TLS_BLOCK` — Caddy custom TLS directives (empty = auto Let's Encrypt)
- `OPT_OUT_CAPTURE` — disable PostHog's own telemetry (recommended: `true`)
- `SEAWEEDFS_DOCKER_NAME`, `DOCKER_REGISTRY_PREFIX` — niche overrides
- `CLICKHOUSE_SERVER_IMAGE` — pin CH version (default `26.3.9.8`)
- `KAFKA_LOG_RETENTION_MS` — Redpanda retention in ms (default: 6h =
  21600000). Wired via `--set redpanda.log_retention_ms` on the kafka
  service. NOTE: Bitnami-style `KAFKA_LOG_RETENTION_*` env vars are
  silently ignored by Redpanda — only this single override works.
- `KAFKA_INGESTION_PARTITIONS` — partitions on `events_plugin_ingestion`
  family (default 4 = 1 per `ingestion-general` replica). Grow before
  scaling replicas — Kafka cannot shrink partitions.
- `POSTHOG_LOG_ENTRIES_TTL_DAYS` — Hog function logs TTL (default 14)
- `POSTHOG_QUERY_LOG_ARCHIVE_TTL_DAYS` — query archive TTL (default 30)

**No longer required** (vs upstream hobby): `REGISTRY_URL`, `POSTHOG_APP_TAG`,
`POSTHOG_NODE_TAG` — all image references in `prod.yml` are hardcoded local tags.

---

## Operational tuning

### Host-level setup (one-time, not in setup-prod)

`bin/setup-prod` warns if RAM < 16 GB, swap < 2 GB, or disk free < 150 GB but
doesn't fix them — those changes need root and modify `/etc/fstab`, and
some VPS types (LXC, restricted Docker hosts) forbid user-controlled swap.
Run these explicitly:

```bash
sudo bin/setup-swap                    # 4 GB swap + vm.swappiness=10 (idempotent)
sudo bin/setup-swap 8G                 # custom size

# Reclaim disk before first build (build is 30-50 GB transient)
docker system prune -a -f --volumes
sudo journalctl --vacuum-time=2d
sudo apt clean
df -h /
```

### Per-project session replay retention

Self-hosted PostHog defaults newly-created teams to **5 years** of replay
retention (hardcoded in `posthog/models/team/team.py`). On a single-host
install this lets SeaweedFS replay blobs grow to TBs over months. After
signup, change for each project in:

> Settings → Replay → Recording retention → "30 Days" → Save

Or update all teams in one shot via Django shell:

```bash
docker compose exec -T web python manage.py shell <<'PY'
from posthog.models import Team
n = Team.objects.update(session_recording_retention_period="30d")
print(f"Updated {n} teams")
PY
```

Valid values: `"30d"`, `"90d"`, `"1y"`, `"5y"`.

### Memory limits — what to do if a container OOM-kills

Watch `docker stats` for 24-48 h after first deploy. Adjust the relevant
anchor at the top of `docker-compose.prod.yml`:

```yaml
x-mem-django-web: &mem-django-web  { mem_limit: 1500m,  memswap_limit: 2g }
```

Bump `mem_limit` for any service consistently above 80 % of its cap.
Lower for any service consistently below 20 %. Caps overcommit physical
RAM by design (sum ≈ 35 GB on a 30 GB box) — they're upper bounds, not
reservations. Real steady-state usage is ~22-25 GB. Swap (set via
`bin/setup-swap`) absorbs simultaneous peaks.

ClickHouse's hard cap is in two places — keep them in sync:

- `docker/clickhouse/config.d.prod/memory_limits.xml` → `<max_server_memory_usage>` (server self-limit)
- `docker-compose.prod.yml` → `x-mem-clickhouse: mem_limit:` (cgroup hard cap)

The cgroup limit should be ~20 % above the server self-limit so CH never
hits the docker OOM-killer (which is unrecoverable) — instead it throws
"Memory limit exceeded" at the query level (recoverable).

### Scaling ingestion-general

The default config runs `ingestion-general` with `replicas: 4` and
`KAFKA_INGESTION_PARTITIONS=4` — exactly 1 partition per consumer under
cooperative-sticky assignment. Each replica is capped at 1.5 GB (6 GB
total). Capacity ceiling is roughly **12-20k msgs/s sustained**, bounded
in practice by Postgres person-resolution + ClickHouse persistence rather
than the Node consumer itself.

The 4/4 baseline is the recommended setpoint for 30 GB / 8-core hardware.
Consumer lag growing on `events_plugin_ingestion` while ingestion-general
sits at <60% CPU usually means PG is the bottleneck, not Kafka — see
[Postgres write contention](#postgres-write-contention) before adding replicas.

To scale beyond the default (only after upgrading hardware):

```yaml
# docker-compose.prod.yml — keep replicas == partitions
ingestion-general:
    deploy:
        replicas: 6
```

```bash
# .env — must be >= replicas. Kafka can ONLY grow partitions, not shrink.
KAFKA_INGESTION_PARTITIONS=6
```

Then:

```bash
docker compose up -d ingestion-general
docker compose restart kafka-init   # idempotently grows partitions in place
```

Cost per added replica: +1.5 GB cgroup budget, +30 PG connections at peak
(lower `POSTGRES_MAX_CONNECTIONS` headroom), +20-50 MB Kafka log per new
partition. Don't go above replicas == partitions — extra replicas just sit
idle in standby (one consumer per partition is the Kafka ceiling).

### Postgres write contention

If you see Kafka consumer lag growing on `events_plugin_ingestion` while
`docker stats` shows ingestion-general at <60% CPU, the bottleneck is
Postgres write contention on `posthog_person` / `posthog_persondistinctid`.
Diagnose with:

```bash
docker compose exec db psql -U posthog -c "
  SELECT relname, n_dead_tup, n_live_tup,
         round(100*n_dead_tup::numeric/NULLIF(n_live_tup, 0), 1) AS dead_pct
  FROM pg_stat_user_tables
  WHERE schemaname='public' AND n_dead_tup > 1000
  ORDER BY n_dead_tup DESC LIMIT 10;
"

docker compose exec db psql -U posthog -c "
  SELECT pid, now() - xact_start AS xact_duration, wait_event_type, wait_event, query
  FROM pg_stat_activity
  WHERE state != 'idle' AND wait_event_type = 'Lock'
  ORDER BY xact_start;
"
```

If `dead_pct > 20%` on `posthog_person*`, autovacuum is falling behind.
Lower `POSTGRES_AUTOVACUUM_SCALE_FACTOR` to `0.02` and restart `db`.

If you see `Lock` waits on the same `posthog_person` rows, you have hot
distinct_ids (e.g. shared API key, single test user generating lots of
events). The fix is application-side — make your distinct_ids actually
distinct, or set `process_person_profile=false` on bot/test traffic so PG
isn't touched at all.

### Web Analytics pre-aggregated tables (currently unfilled)

Upstream PostHog Cloud uses Dagster to maintain pre-aggregated rollup
tables (`web_pre_aggregated_stats`, `web_pre_aggregated_bounces`) that
back Web Analytics tiles for 10-100× faster queries on multi-month
windows. The CH tables exist on this fork (created by migration
`0130_add_web_analytics_utc_hourly_tables.py`), the insert SQL is in
`posthog/models/web_preaggregated/sql.py`, and the read path checks
the `useWebAnalyticsPreAggregatedTables` modifier on each query
(`web_overview.py:43`, `notable_changes.py:98`). UI toggle exists in
`WebAnalyticsHeaderButtons.tsx`.

**What's missing:** the Dagster scheduler that fills the tables. This
fork has no Dagster service in `docker-compose.prod.yml`, so the
hourly/daily fill jobs never fire. Tables stay empty. The toggle in the
UI does nothing useful.

To enable on this fork, replace Dagster with a Celery beat task (~80
lines): port the partition-swap logic from
`products/web_analytics/dags/web_preaggregated.py:80-180` into
`posthog/tasks/web_preaggregated.py`, register an hourly + daily entry
in `posthog/celery.py` beat schedule. No new infra needed.

Skip this entirely if your install only ingests mobile / non-`$pageview`
events — preagg schema is web-shaped (`pathname`, `host`, `referring_domain`,
`utm_*`) and most fields stay null on mobile traffic.

### Load testing the capture endpoint

Use `bin/loadtest-capture` (vegeta wrapper, auto-installs vegeta on apt/brew,
reads token+domain from `.env`):

```bash
bin/loadtest-capture                    # ramp 100 → 10k req/s, 30s each
bin/loadtest-capture --rate 1000 --duration 5m
bin/loadtest-capture --cleanup          # delete loadtest events from ClickHouse
bin/loadtest-capture --help
```

The script prints the right `docker stats`, Kafka lag, and ClickHouse
ingestion queries to run in another terminal while the test is in flight.

Realistic numbers for the box-sized config (30 GB / 8 cores):

| Metric | Sustained | Burst | Bottleneck |
|---|---|---|---|
| `/capture` req/s | 15 000-30 000 | 60 000+ | 3 capture replicas × ~5-10k/replica; then Kafka write |
| Events/s through `ingestion-general` | 12 000-20 000 | 25 000+ | 4 replicas × 3-5k Node consumers each |
| Events/s persisted to CH | 2 000-5 000 | 8 000 | Postgres person resolution (`posthog_person*` write contention) |
| Concurrent insight queries | 5-10 | 20 | ClickHouse memory + CPU |

If Kafka consumer lag grows monotonically across the test → `ingestion-general`
is overloaded. Default is already `replicas: 4` matched to 4 partitions; to go
higher, follow [Scaling ingestion-general](#scaling-ingestion-general) above
(replicas and `KAFKA_INGESTION_PARTITIONS` move together).

---

## Backups

Off-host backups to a Google Cloud Storage bucket. PG daily, CH events
daily (current month) + monthly (frozen, never deleted), CH persons
weekly. Retention is set by `docker/backup/lifecycle.json` — see
[`docker/backup/README.md`](docker/backup/README.md) for the full
operator manual (restore runbook, schedule editing, key rotation, DR
sequence, what isn't covered).

### Enable

1. Create a GCS bucket and an HMAC key pair (Cloud Console → *Storage*
   → *Settings* → *Interoperability*). Grant `roles/storage.objectAdmin`
   on the bucket to the SA backing the HMAC.
2. Add to `.env`:

   ```bash
   GCS_BUCKET=gs://my-posthog-backups
   GCS_HMAC_KEY=GOOG1...
   GCS_HMAC_SECRET=...
   COMPOSE_PROFILES=backup        # or e.g. cdp,backup
   ```
3. Apply the bucket lifecycle (one-shot, replaces existing policy):

   ```bash
   gcloud storage buckets update gs://my-posthog-backups \
       --lifecycle-file=docker/backup/lifecycle.json
   ```
4. Build and start:

   ```bash
   docker compose up -d --build backup
   ```

First PG dump fires at the next 02:00 UTC tick. Force one immediately
to verify wiring:

```bash
docker compose exec backup /usr/local/bin/backup-scripts/backup-pg.sh
```

### Verify it's working

```bash
docker compose logs -f backup                                                      # live tail
docker compose exec backup /usr/local/bin/backup-scripts/status.sh                 # local sentinels
docker compose exec backup /usr/local/bin/backup-scripts/verify.sh                 # GCS-side
docker compose ps backup                                                           # healthcheck
```

### Restore / disaster recovery

See [`docker/backup/README.md`](docker/backup/README.md). All restores
are in-place destructive; stop dependent services first
(`temporal`, `cyclotron-janitor`, and `batch-import-worker` included)
and the scripts will prompt for explicit `YES` confirmation. Restore
scripts hard-refuse if active connections are detected — they print
the exact stop command and exit non-zero rather than corrupting state.

> **`.env` is NOT backed up by this stack.** Keep an out-of-band copy
> (password manager, separate encrypted bucket, `git-crypt`'d repo).
> Losing the host AND the `.env` simultaneously means the GCS backups
> are unrecoverable.

---

## Events export

Daily Mixpanel-shape JSONL.gz of `posthog.sharded_events` to a SEPARATE
GCS bucket, one file per (team, day), forever-retained. Different bucket,
HMAC pair, and lifecycle from the disaster-recovery Backups above —
analytical archive, not DR. See
[`docker/posthog-events-export/README.md`](docker/posthog-events-export/README.md)
for the full operator manual (enable, force a run, verify, restore,
schedule, retention) and
[`ee/scripts/posthog_events_export/README.md`](ee/scripts/posthog_events_export/README.md)
for the script-level docs (schema mapping, CLI flags, manual backfills).

Quick enable:

```bash
# .env
EVENTS_EXPORT_GCS_BUCKET=gs://vibes-analytics-events
EVENTS_EXPORT_GCS_HMAC_KEY=GOOG1...
EVENTS_EXPORT_GCS_HMAC_SECRET=...
COMPOSE_PROFILES=backup,events-export

docker compose --profile events-export up -d --build posthog-events-export
```

---

## Mixpanel migration

One-shot historical import: pull events from Mixpanel, stage in GCS, replay
into this PostHog instance via `batch-import-worker`. Idempotent end-to-end
(Mixpanel `$insert_id` → deterministic UUIDv5 → ClickHouse `ReplacingMergeTree`
dedup), so reruns are safe.

Three stages, each with its own README:

1. **Export** — [`ee/scripts/mixpanel_export/README.md`](ee/scripts/mixpanel_export/README.md):
   Python script that streams the Mixpanel Export API to GCS as `.jsonl.gz`.
2. **Split** — [`ee/scripts/mixpanel_splitter/README.md`](ee/scripts/mixpanel_splitter/README.md):
   Cloud Run Job that fans out monthly files into per-day `.jsonl.gz` files
   (the granularity `batch-import-worker` consumes).
3. **Import** — [`ee/scripts/mixpanel_import/RUNBOOK.md`](ee/scripts/mixpanel_import/RUNBOOK.md):
   the operator runbook — preflight checks, kicking off `batch-import-worker`
   under the `migration` compose profile, monitoring Kafka lag, verifying
   ClickHouse counts, cleanup. Read this end-to-end before starting.

Output of stage 2 is structurally identical to what `events-export` produces
(Mixpanel-shape JSONL.gz), so the same `batch-import-worker` consumes both —
restoring an `events-export` archive uses the same Phase 2-onwards path as
the Mixpanel migration runbook.

---
