# Backup container

Off-host backups to a Google Cloud Storage bucket for self-hosted PostHog.

This is the operator manual. The main `README.prod.md` only covers
"enable, verify it's running, restore in disaster". Everything below is
the long form: editing schedules, restore mechanics, key rotation,
deliberate trade-offs, and what isn't covered.

## What's in this directory

| File                            | Role                                                                 |
|---------------------------------|----------------------------------------------------------------------|
| `Dockerfile`                    | Image: alpine + aws-cli + pg client + curl + tini + crond           |
| `crontab`                       | Cron schedule (when each backup runs)                                |
| `lifecycle.json`                | GCS bucket lifecycle policy (storage-class transitions + deletes)    |
| `scripts/_lib.sh`               | Shared helpers (logging, GCS auth wrapper, CH HTTP wrapper)         |
| `scripts/backup-pg.sh`          | Daily `pg_dumpall \| gzip \| aws s3 cp -`                           |
| `scripts/backup-ch-current-month.sh` | Daily CH `BACKUP TABLE … PARTITION ID 'YYYYMM' TO S3()`          |
| `scripts/backup-ch-freeze-last-month.sh` | Monthly CH `BACKUP TABLE … PARTITION ID '<prev-month>'`     |
| `scripts/backup-ch-persons.sh`  | Weekly CH `BACKUP DATABASE posthog EXCEPT TABLES …`                  |
| `scripts/restore-pg.sh`         | In-place destructive PG restore (with auto pre-restore snapshot)     |
| `scripts/restore-ch-events.sh`  | In-place destructive partition restore (with auto FREEZE rollback)   |
| `scripts/restore-ch-persons.sh` | In-place destructive drop+restore (no merge — rewinds to backup)     |
| `scripts/status.sh`             | Local sentinel summary table                                         |
| `scripts/verify.sh`             | GCS-side manifest validation (size + age)                            |
| `scripts/healthcheck.sh`        | `docker compose ps` healthcheck (per-job grace windows)              |

## What's backed up, on what cadence, with what retention

| Class | Cadence | Retention (set by GCS lifecycle) |
|---|---|---|
| Postgres full dump | daily | 7d Standard → 30d Nearline → 90d Coldline → delete |
| CH `sharded_events` (current month) | daily | 3d, then deleted |
| CH `sharded_events` (closed month) | once on month rollover, then immutable | 60d Standard → 365d Coldline → Archive (NEVER deleted) |
| CH everything-except-events (persons / sessions / etc.) | weekly | 28d Standard → 56d Nearline → delete |

The "frozen-month never deleted" choice is intentional — every monthly
`YYYYMM.zip` is a complete unit and deleting any single one creates a
permanent gap that no future restore can fill. Cost is bounded (typical
small install: ~1-10 GB/month in Coldline = pennies/year).

What's NOT backed up: SeaweedFS replay blobs, Kafka, Redis, Zookeeper,
MinIO. All caches or rebuildable. See [What's not covered](#whats-not-covered).

## Editing the schedule or retention

| Change | Edit | Re-apply |
|---|---|---|
| Schedule (cadence, time-of-day) | `crontab` | `docker compose up -d --build backup` |
| Retention (storage classes, delete age) | `lifecycle.json` | `gcloud storage buckets update --lifecycle-file=docker/backup/lifecycle.json gs://<bucket>` |
| Excluded CH tables | `scripts/backup-ch-persons.sh` (`EXCEPT TABLES` clause) AND `scripts/restore-ch-persons.sh` (`EXCEPT_TABLES_SQL`) | `docker compose up -d --build backup` |
| Min-size / age thresholds for `verify.sh` | `scripts/verify.sh` | `docker compose up -d --build backup` |

The `lifecycle.json` API replaces the entire policy on each call. To
clear: `gcloud storage buckets update --clear-lifecycle gs://<bucket>`.

## Daily ops

```bash
# Live tail (cron output, individual job stdout/stderr)
docker compose logs -f backup

# Status table -- "did the cron RUN?" (reads local sentinel files)
docker compose exec backup /usr/local/bin/backup-scripts/status.sh

# Verify -- "are GCS artifacts uploaded, sane size, fresh?" (talks to GCS)
docker compose exec backup /usr/local/bin/backup-scripts/verify.sh
docker compose exec backup /usr/local/bin/backup-scripts/verify.sh pg     # one class

# Compose-level health (driven by healthcheck.sh)
docker compose ps backup

# Force-run a single backup NOW (e.g. before risky maintenance)
docker compose exec backup /usr/local/bin/backup-scripts/backup-pg.sh
docker compose exec backup /usr/local/bin/backup-scripts/backup-ch-current-month.sh

# Watch a CH BACKUP / RESTORE while it's running
docker compose exec clickhouse clickhouse-client \
    --password="$(grep ^CLICKHOUSE_PASSWORD .env | cut -d= -f2)" \
    --query "SELECT id, name, status, formatReadableSize(total_size) AS size, error
             FROM system.backups ORDER BY start_time DESC LIMIT 5"
```

## Restore runbook

All restores are **in-place destructive** on the live PG/CH instances.
Stop dependent services first or expect crash-loops during the restore.

### Postgres (full cluster)

```bash
# 1. Stop everything that talks to PG (including `temporal` itself,
#    which holds its own connections, and cyclotron-janitor under cdp).
docker compose stop \
    web worker temporal-django-worker temporal plugins \
    ingestion-general ingestion-sessionreplay ingestion-error-tracking \
    ingestion-logs ingestion-traces \
    recording-api hypercache-server \
    capture replay-capture property-defs-rs feature-flags cymbal \
    cyclotron-janitor

# 2. Restore (defaults to the most recent PG dump in GCS).
docker compose exec -it backup /usr/local/bin/backup-scripts/restore-pg.sh
# or pin to a specific dump:
# docker compose exec -it backup /usr/local/bin/backup-scripts/restore-pg.sh \
#     postgres/posthog-20260506T020000Z.sql.gz

# 3. Bring everything back.
docker compose up -d
```

The restore script auto-snapshots the current PG state to
`/var/run/backup/pre-restore-pg-<TS>.sql.gz` (in the backup container's
named volume) BEFORE importing. Multiple restores leave multiple
snapshots — pick the most recent if you need to revert:

```bash
docker compose exec -it backup bash -c '
    snap=$(ls -t /var/run/backup/pre-restore-pg-*.sql.gz | head -n 1)
    echo "Reverting from $snap"
    gunzip -c "$snap" \
    | PGPASSWORD=$POSTHOG_DB_PASSWORD psql --set ON_ERROR_STOP=1 \
        -h db -U posthog -d postgres
'
```

### ClickHouse `sharded_events` (single-month partition)

```bash
# Restore April 2026 from the frozen monthly snapshot.
docker compose exec -it backup /usr/local/bin/backup-scripts/restore-ch-events.sh 202604

# Restore the current month from yesterday's daily snapshot.
docker compose exec -it backup /usr/local/bin/backup-scripts/restore-ch-events.sh 202605 current

# Pin to an explicit GCS object key.
docker compose exec -it backup /usr/local/bin/backup-scripts/restore-ch-events.sh \
    202604 clickhouse/events/frozen/202604.zip
```

The restore script `ALTER TABLE … FREEZE PARTITION ID '…'` BEFORE
dropping the partition. The freeze creates hardlinks under
`/var/lib/clickhouse/shadow/pre-restore-<PARTITION>-<TS>/` (cheap, no
copy) and gives you an in-place rollback path: re-attach those parts
manually via `ALTER TABLE … ATTACH PART …` and clean the freeze with
`SYSTEM UNFREEZE WITH NAME 'pre-restore-…'` once the restore is verified.

### ClickHouse persons + everything-except-events

```bash
docker compose exec -it backup /usr/local/bin/backup-scripts/restore-ch-persons.sh
```

The script enumerates every table in the `posthog` CH database that
isn't in the EXCEPT list (events, session_replay, app_metrics,
log_entries, query_log_archive, ingestion_warnings, writable_events,
events_recent), DROPs them, then restores fresh from the backup. This
is "REPLACE", not "MERGE" — old rows that aren't in the backup are
gone. There is no rollback path once DROP runs; if RESTORE fails after
DROP, re-run with a known-good source.

Most CH metadata in this dump (person tables, sessions, channel_type,
exchange_rate) is also derivable from PG via the personhog rebuild
path, so "restore PG first, then re-derive CH persons" is often
cleaner than restoring this archive. Use this restore only when
CH-side person tables are confirmed corrupt AND you can't or don't
want to wait for the rebuild.

## Key rotation (GCS HMAC pair)

Rotate without downtime by overlapping old + new keys:

```bash
# 1. Cloud Console: create a NEW HMAC key for the same SA. Now you have
#    two valid keys (old + new).
# 2. Edit .env: replace GCS_HMAC_KEY / GCS_HMAC_SECRET with the new pair.
# 3. Restart only the backup container -- nothing else uses these keys.
docker compose up -d --force-recreate backup
# 4. Run a backup manually to confirm the new key works.
docker compose exec backup /usr/local/bin/backup-scripts/backup-pg.sh
# 5. Cloud Console: deactivate (then delete) the old HMAC key.
```

If you rotate keys then realize an old backup was created with a
permission scope that's gone — the .zip in GCS itself doesn't embed
the key, so you can read it back with any HMAC pair that has bucket
access.

> **HMAC visibility inside CH.** ClickHouse's `BACKUP TABLE … TO
> S3('url', 'key', 'secret')` syntax stores the literal SQL (HMAC
> secret included) in `system.backups.name` and `system.query_log`.
> Anyone with the CH `default` password can read it. In this stack
> that password is already the keys-to-the-kingdom (full read/write
> on all events and persons), so the incremental risk is small — but
> if you ever expose CH beyond docker-internal, migrate to a
> `<storage_configuration>` disk of type `s3` and use `BACKUP … TO
> Disk('gcs_backup', '…')` instead.

## Pausing backups

```bash
# Pause without losing the schedule:
docker compose stop backup

# crond doesn't catch up missed ticks -- a paused window is just a
# missed window, not deferred work. Restart when ready:
docker compose start backup
```

## What's not covered

- **PITR for Postgres.** Self-host already runs with
  `synchronous_commit=off` (200ms RPO accepted), so daily dumps are
  consistent with that posture. PITR via wal-g would push RPO to
  seconds but adds non-trivial ops overhead. Add only if your data
  writes can't tolerate 24h loss on host destruction.
- **Automated full-restore drills.** `verify.sh` is manifest-only — it
  catches the most common silent-failure mode (truncated upload, stale
  cron) but doesn't prove RESTORE will succeed. For that, restore into
  a throwaway compose project on a separate VM weekly. Untested
  backups are not backups.
- **SeaweedFS / MinIO blob replication.** Use `rclone sync` to a
  separate GCS prefix on its own cadence. Blob stores have very
  different size profiles (10x-100x events) and need their own
  bandwidth + cost budget.
- **Kafka topic backup.** Kafka in this stack is a transient buffer
  between capture and ingestion workers. Topic state at restore time
  is stale; clients re-emit on retry.
- **Redis snapshots.** Redis holds short-lived caches and rate-limit
  counters. All recoverable from steady-state traffic in minutes.

## Disaster-recovery sequence (host loss)

Order matters when rebuilding from zero:

1. Provision a fresh host, restore the repo + `.env`.

   **`.env` is NOT backed up by this stack.** It contains every
   credential (PG/CH passwords, GCS HMAC pair, secrets) — without it,
   the GCS bucket is unreadable. Keep an out-of-band copy: a password
   manager, a separate encrypted bucket, or `git-crypt`'d in a
   private repo. Losing the host AND the `.env` simultaneously means
   the backups are unrecoverable.

   Keep `CLICKHOUSE_SERVER_IMAGE` pinned to the same version that
   produced the backups — version mismatch is a top cause of restore
   failures.
2. `bin/setup-prod` to bring up the empty stack with profile=backup.
3. Stop dependents (the long `docker compose stop ...` line above).
4. `restore-pg.sh latest`. Fast; gets you team IDs, persons (PG
   ground truth), dashboards, feature flag config back.
5. `restore-ch-persons.sh latest`. Or skip and rely on personhog's
   PG → CH rebuild path if you can wait.
6. `restore-ch-events.sh <YYYYMM>` for each month you want back.
   Restore recent months first (most likely to be queried). Old
   frozen months can be deferred — they're already safe in Coldline.
7. `docker compose up -d` to bring web/worker/ingestion back.
8. Verify: hit `/api/projects/@current/insights/` and check counts
   roughly match expectations. Run `verify.sh` against GCS to confirm
   nothing in the bucket disappeared mid-restore.
