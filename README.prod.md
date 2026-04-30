# Self-Hosted PostHog (production-ish)

Run PostHog from your forked repo with locally-built images, no upstream
registry, no separate installer binary. Optimized for a single-host VPS
deployment that you can patch and redeploy from `git`.

## Files

| Path                          | Purpose                                                 |
|-------------------------------|---------------------------------------------------------|
| `docker-compose.prod.yml`     | Production compose file (forked from `docker-compose.hobby.yml`) |
| `bin/setup-prod`              | Idempotent per-host bootstrap script                    |
| `.env.example.prod`           | Template for `.env` (manual, NOT generated)             |
| `compose/start`               | Web entrypoint (created by `setup-prod`)                |
| `compose/temporal-django-worker` | Temporal worker entrypoint (created by `setup-prod`) |
| `compose/wait`                | TCP wait-for-deps script (created by `setup-prod`)      |
| `share/GeoLite2-City.mmdb`    | GeoIP database (downloaded by `setup-prod`)             |

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
sed -i.bak 's|CHANGE_ME_your.domain.tld|posthog.example.com|' .env
sed -i.bak "s|CHANGE_ME_run_openssl_rand_hex_32|$(openssl rand -hex 32)|" .env
sed -i.bak "s|CHANGE_ME_run_openssl_rand_hex_16|$(openssl rand -hex 16)|" .env
sed -i.bak "s|CHANGE_ME_postgres_openssl_rand_hex_24|$(openssl rand -hex 24)|" .env
sed -i.bak "s|CHANGE_ME_minio_openssl_rand_hex_24|$(openssl rand -hex 24)|" .env
sed -i.bak "s|CHANGE_ME_clickhouse_openssl_rand_hex_24|$(openssl rand -hex 24)|" .env
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
   installed and the daemon is reachable; warns if RAM &lt; 8 GB or disk &lt; 50 GB.
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
| ClickHouse `system_log` TTLs via `docker/clickhouse/config.d.prod/system_log_ttl.xml` | new file, mounted in prod.yml | Without TTLs, `query_log` / `trace_log` / `metric_log` / `part_log` grow unbounded — tens of GB in a few weeks on a busy install. Now 7d retention. |
| ClickHouse memory caps via `docker/clickhouse/config.d.prod/memory_limits.xml` | new file, mounted in prod.yml | Upstream `config.xml` sets `max_server_memory_usage_to_ram_ratio=0.9`. On a 30 GB box that means CH eats 27 GB and OOM-kills the rest of the stack under any load. Overlay caps to 8 GB hard limit + tightens `max_thread_pool_size` from 10000 (sized for 64-core servers) to 1000. |
| Per-service `mem_limit` via YAML anchors | prod.yml top-of-file | Without these, a single runaway container takes the whole box. Sized for 30 GB / 8 cores. Sum of caps (~35 GB) intentionally overcommits — caps are spike absorbers, not reservations. 4 GB host swap (see `bin/setup-swap`) backstops simultaneous peaks. |
| Redis raised from 200 MB to 1 GB + `volatile-lru` policy | prod.yml | Base.yml's 200 MB / `allkeys-lru` is too small for an instance that's also Celery broker + result backend + hypercache + flag cache + session-replay state. `allkeys-lru` evicts in-flight Celery messages under pressure; `volatile-lru` only evicts keys with TTL (caches), so queues survive. |
| Postgres tuning via `command:` overrides (`shared_buffers=1GB` etc.) | prod.yml + `.env` | Image defaults (`shared_buffers=128MB`) are sized for embedded use. PG gets a 4 GB cgroup (with 5 GB swap headroom for transient `work_mem × max_connections` spikes — worst case ~5.5 GB at 200 conns × 16 MB sort/hash). Tunable via `POSTGRES_*` in `.env` without rebuilding. Lower `POSTGRES_MAX_CONNECTIONS` to 120 if you also want to lower the cgroup cap. |
| Postgres write-throughput tuning (`synchronous_commit=off`, `max_wal_size=4GB`, autovacuum) | prod.yml + `.env` | Default fsync-on-commit + 1GB WAL + 5min checkpoints stalls writers under sustained ingest. New defaults give 3-5x write throughput at the cost of losing the last <200ms of unflushed transactions on a crash — acceptable for analytics (events replay from Kafka's 6h retention). NOT acceptable if you store anything you can't re-derive: flip `POSTGRES_SYNCHRONOUS_COMMIT=on` in `.env`. |
| `ingestion-general` runs with `replicas: 2` | prod.yml | Single replica saturates around 1k events/s on this hardware (person-resolution + Postgres + ClickHouse writes). Two consumers in the same group parallelize work because Rust capture partitions Kafka by `{token}:{distinct_id}` — same user always lands on same partition, so per-user ordering survives. |
| `events_plugin_ingestion` + `_overflow` pre-created with 3 partitions | prod.yml `kafka-init` | Redpanda auto-creates topics with 1 partition by default; with only 1 partition, the second `ingestion-general` replica sits idle in standby. 3 partitions = 2 active consumers + 1 spare for rebalances. Tunable via `KAFKA_INGESTION_PARTITIONS` for higher replica counts. |
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

### Required env vars

`.env.example.prod` lists everything. Required:

- `DOMAIN` — your hostname
- `POSTHOG_SECRET` — Django secret (`openssl rand -hex 32`)
- `ENCRYPTION_SALT_KEYS` — token encryption (`openssl rand -hex 16`)
- `POSTHOG_DB_PASSWORD` — Postgres `posthog` user (`openssl rand -hex 24`)
- `OBJECT_STORAGE_PASSWORD` — MinIO root (`openssl rand -hex 24`)
- `CLICKHOUSE_PASSWORD` — ClickHouse `default` user (`openssl rand -hex 24`)

Optional:

- `TLS_BLOCK` — Caddy custom TLS config (empty = auto Let's Encrypt)
- `OPT_OUT_CAPTURE` — disable PostHog's own telemetry (recommended: `true`)
- `SEAWEEDFS_DOCKER_NAME`, `DOCKER_REGISTRY_PREFIX` — niche overrides
- `CLICKHOUSE_SERVER_IMAGE` — pin CH version (default `26.3.9.8`)
- `KAFKA_LOG_RETENTION_MS`, `KAFKA_LOG_SEGMENT_SIZE` — Redpanda retention
  (defaults: 1h / 128 MB)
- `CLICKHOUSE_SYSTEM_LOG_TTL_DAYS` — CH `system_log` TTL (default 7)
- `POSTHOG_LOG_ENTRIES_TTL_DAYS` — Hog function logs TTL (default 14)
- `POSTHOG_QUERY_LOG_ARCHIVE_TTL_DAYS` — query archive TTL (default 30)

**No longer required** (vs upstream hobby): `REGISTRY_URL`, `POSTHOG_APP_TAG`,
`POSTHOG_NODE_TAG` — all image references in `prod.yml` are hardcoded local tags.

---

## Operational tuning

### Host-level setup (one-time, not in setup-prod)

`bin/setup-prod` warns if RAM < 16 GB, swap < 2 GB, or disk free < 100 GB but
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

The default config runs `ingestion-general` with `replicas: 2`, parallelized
via 3 Kafka partitions on `events_plugin_ingestion` and its overflow topic.
Each replica is capped at 1.5 GB. Capacity ceiling is roughly **2-3k
events/s sustained** on this hardware, depending on hog functions / person
resolution cache hit rate.

To scale further:

```yaml
# docker-compose.prod.yml
ingestion-general:
    deploy:
        replicas: 4
```

```bash
# .env — partitions = replicas + 1 for rebalance headroom
KAFKA_INGESTION_PARTITIONS=5
```

Then:

```bash
docker compose up -d ingestion-general
docker compose restart kafka-init   # idempotently grows partitions in place
```

Costs: each replica adds 1.5 GB to the cgroup budget. At 4 replicas you're
adding 4.5 GB on top of the existing 3 GB — keep an eye on `docker stats`
and free disk for the extra Kafka partition data (~20-50 MB per partition).

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
| `/capture` req/s | 5 000-10 000 | 20 000+ | Rust capture is fast; Kafka write |
| Events/s persisted to CH | 500-2 000 | 5 000 | `ingestion-general` CPU + Postgres person resolution |
| Concurrent insight queries | 5-10 | 20 | ClickHouse memory + CPU |

If Kafka consumer lag grows monotonically across the test → `ingestion-general`
is overloaded; bump its replicas in `docker-compose.prod.yml`:

```yaml
    ingestion-general:
        # ... existing ...
        deploy:
            replicas: 2
```

---
