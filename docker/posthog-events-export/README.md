# PostHog daily events export container

Container-based daily export of `posthog.sharded_events` to a GCS bucket
in **Mixpanel-shaped JSONL.gz** (one file per team per day). Same
operational shape as `docker/backup/`.

```text
gs://vibes-analytics-events/posthog-events/team-<id>/YYYY/MM/YYYY-MM-DD.jsonl.gz
```

| Layer            | Path / Image                                |
|------------------|---------------------------------------------|
| Python script    | `ee/scripts/posthog_events_export/export_daily.py` |
| Container build  | `docker/posthog-events-export/Dockerfile`   |
| Compose service  | `posthog-events-export` (profile `events-export`) |
| Cron schedule    | 06:00 UTC daily                             |
| Sentinel volume  | `posthog_events_export_state` → `/var/run/events-export/` |
| Destination      | `gs://vibes-analytics-events/posthog-events/team-<id>/...` |
| Retention        | **forever** (no lifecycle.json shipped — bucket is Coldline by default at the bucket level) |

See [`ee/scripts/posthog_events_export/README.md`](../../ee/scripts/posthog_events_export/README.md)
for the python-script-level docs (schema mapping, CLI flags, manual
backfills).

## What this container does

1. `crond -f` runs as PID 1 via tini.
2. Every day at 06:00 UTC, `cron` invokes
   `/usr/local/bin/events-export-scripts/export-events.sh`, which:
   - exports GCS HMAC creds as standard `AWS_*` env vars
   - rewrites `gs://...` → `s3://...` + `AWS_ENDPOINT_URL=https://storage.googleapis.com`
     (because fsspec's `gcsfs` doesn't speak HMAC)
   - runs `python export_daily.py --yesterday --output s3://<bucket>/`
   - touches `/var/run/events-export/events-export.last-ok` on success.
3. `healthcheck.sh` reads the sentinel and reports unhealthy if it's
   missing past the start-period grace, or stale beyond 26h.
4. `verify.sh` (manual) lists `posthog-events/team-*/` in GCS, prints
   per-team object count + size + age, fails on any team with the latest
   object too small or too old.

## Why a separate container (instead of sharing `backup`)?

Different concerns:

- `backup/` is **disaster recovery**. ZIP'd `BACKUP TO S3()` artifacts,
  daily cadence per artifact class, fast restore as the priority.
- `posthog-events-export/` is **analytical archive**. Per-team JSONL.gz
  for re-import / external analysis, forever retention, indefinite
  growth in a separate bucket.

Mixing them would mean:

- One healthcheck that's unhealthy if either job stalls (false positives).
- Shared lifecycle.json — but the buckets have opposite policies
  (backups age + delete, events forever-retain).
- Restoring backups while exports stream would compete for ClickHouse
  HTTP slots.

Two containers, two profiles, two health domains. ~70 MB extra image
size — acceptable for clear separation.

## Why HMAC instead of GCS native auth?

Same reason as `docker/backup/`: HMAC is a single env-var pair shared
between `aws-cli`, `s3fs`, and ClickHouse `BACKUP TO S3()`. No
service-account JSON, no metadata server, no `gcloud-sdk` image, no
ADC cache file mount. The trade-off is that `fsspec/gcsfs` (which DOES
support HMAC… nope, it doesn't actually — only OAuth/ADC/SA-JSON) can't
be used directly. We use `fsspec/s3fs` + GCS interop endpoint instead.
Functionally identical for the operations we need (PUT, GET, LIST,
DELETE).

## Files

```text
docker/posthog-events-export/
├── Dockerfile           # alpine + python3 + fsspec + s3fs + crond + tini
├── crontab              # 0 6 * * * → export-events.sh
├── README.md            # this file
└── scripts/
    ├── _lib.sh          # logging, sentinels, GCS bucket-name helpers
    ├── export-events.sh # cron entrypoint
    ├── status.sh        # local sentinel state (human-readable)
    ├── verify.sh        # GCS-side validation (manifest probe)
    └── healthcheck.sh   # compose healthcheck (sentinel age check)
```

## Required `.env` keys

```bash
# CH password (already in .env if you've completed the regular setup)
CLICKHOUSE_PASSWORD=...

# Dedicated HMAC pair for the analytics bucket. Create in Cloud Console →
# Storage → Settings → Interoperability → Create access key for service
# account. The SA needs roles/storage.objectAdmin on
# gs://vibes-analytics-events. Use a DIFFERENT SA from the backup HMAC
# so a leak of one doesn't compromise the other.
EVENTS_EXPORT_GCS_BUCKET=gs://vibes-analytics-events
EVENTS_EXPORT_GCS_HMAC_KEY=GOOG1...
EVENTS_EXPORT_GCS_HMAC_SECRET=...

# Add events-export to your active profiles (alongside cdp, backup, etc.):
COMPOSE_PROFILES=backup,events-export
```

## Enable

```bash
docker compose --profile events-export build posthog-events-export
docker compose --profile events-export up -d posthog-events-export
```

First export fires at the next 06:00 UTC tick. Force one immediately to
verify wiring:

```bash
docker compose exec posthog-events-export \
    /usr/local/bin/events-export-scripts/export-events.sh
```

For a backfill of historical data (covers all teams found in CH for that
range):

```bash
docker compose exec posthog-events-export \
    /usr/local/bin/events-export-scripts/export-events.sh \
    --range 2024-01-01 2024-12-31 --concurrency 2
```

For a single team / single day:

```bash
docker compose exec posthog-events-export \
    /usr/local/bin/events-export-scripts/export-events.sh \
    --date 2024-03-28 --team-ids 1
```

## Verify it's working

```bash
docker compose logs -f posthog-events-export                                            # live tail
docker compose exec posthog-events-export /usr/local/bin/events-export-scripts/status.sh   # local sentinels
docker compose exec posthog-events-export /usr/local/bin/events-export-scripts/verify.sh   # GCS-side
docker compose ps posthog-events-export                                                 # healthcheck
```

`status.sh` answers "did the cron RUN successfully?" (one row,
`events-export`).
`verify.sh` lists `posthog-events/team-*/` in GCS and asserts per-team:

- at least one object exists,
- latest object size ≥ 200 B,
- latest object age ≤ 30 h.

Sample healthy `verify.sh` output:

```text
[2026-05-07T06:05:14Z] [verify] verifying gs://vibes-analytics-events/posthog-events/ (filter=all)
team-1: count=42 total_bytes=    1_823_104_211 latest_size=    47_832_105 latest=vibes-analytics-events/posthog-events/team-1/2026/05/2026-05-06.jsonl.gz
team-2: count=42 total_bytes=       381_204_551 latest_size=     8_104_882 latest=vibes-analytics-events/posthog-events/team-2/2026/05/2026-05-06.jsonl.gz
VERIFY OK
```

## Restore / re-import

The whole point of this archive is round-trippability. Per-team imports
follow the existing `ee/scripts/mixpanel_import/RUNBOOK.md`. To import
team-1's archive:

```bash
# In ee/scripts/mixpanel_import/, point GCS_PREFIX at the team-1 subtree:
export GCS_BUCKET=vibes-analytics-events
export GCS_PREFIX='posthog-events/team-1/'
python create_import.py --team-id 1   # or whichever team to ingest into
```

The Rust `batch-import-worker` walks the prefix recursively. Re-imports
are idempotent (CH ReplacingMergeTree dedupes on `$insert_id`).

## Schedule

Single cron entry, edit in `crontab` and rebuild:

```text
0 6 * * *   flock -n /tmp/events-export.lock /usr/local/bin/events-export-scripts/export-events.sh
```

`flock -n` prevents re-entry: a multi-team backfill that overruns the
24h cycle just gets its next tick skipped (logged as
`flock: failed to acquire lock`), no queue buildup.

## Known limitations

- **No row-level consistency check.** `verify.sh` validates manifest
  size + age, not byte-level integrity. If a `.jsonl.gz` is corrupted
  in transit at the HTTP layer (which both GCS and the python pipeline
  retry-protect against, but not infallibly), `verify.sh` will still pass
  it. Spot-check periodically:
  ```bash
  gsutil cat gs://vibes-analytics-events/posthog-events/team-1/2026/05/2026-05-06.jsonl.gz \
      | gunzip | head -1 | jq .
  ```
- **`elements_chain`, `created_at`, `_offset` are dropped.** If you need
  autocapture detail or original ingest timestamps for forensic queries,
  this archive isn't the right tool — query CH directly or use the
  `backup/` BACKUP TO S3() artifacts (which preserve full schema).
- **No FINAL by default.** Output may contain physical-row duplicates
  pre-merge. Re-import dedup handles them; if you're loading into a
  non-PostHog destination (BigQuery), pass `--final` to the
  `export-events.sh` invocation (3-10x slower).
- **No Kafka audit trail.** Unlike upstream PostHog Cloud's CDP
  destinations, this exporter doesn't log per-event delivery — it's a
  bulk warehouse pattern, not a streaming pipeline. The cron sentinel +
  `verify.sh` are the only liveness signals.

## Bumping CH connection settings

`export-events.sh` defaults `CLICKHOUSE_HOST=clickhouse` and
`CLICKHOUSE_PORT_HTTP=8123` — works inside the prod compose network
unmodified. To point at a different CH (e.g. a read replica), override
in `.env`:

```bash
CLICKHOUSE_HOST=clickhouse-replica
CLICKHOUSE_PORT_HTTP=8443
CLICKHOUSE_USER=readonly_export
```

…and rebuild the container so they're in scope at cron time.

## Bumping the schedule

The default 06:00 UTC was chosen specifically AFTER `docker/backup/`'s
05:30 cleanup tick to avoid CH HTTP slot contention. If you want a
different time, edit `crontab` and `docker compose build` —
healthcheck cadence (26h MAX_AGE) compensates for any time shift up to
a few hours. Beyond that, also adjust `MAX_AGE` in `healthcheck.sh` and
`status.sh`.

## Bumping retention

The destination bucket has NO lifecycle policy applied by default
(forever retention). If you want to layer a transition rule (e.g.
Coldline → Archive at 1y) without a Delete rule:

```bash
cat <<EOF > lifecycle.json
{
  "lifecycle": {
    "rule": [
      { "action": { "type": "SetStorageClass", "storageClass": "ARCHIVE" },
        "condition": { "age": 365 } }
    ]
  }
}
EOF
gcloud storage buckets update gs://vibes-analytics-events \
    --lifecycle-file=lifecycle.json
```

Don't add a `Delete` rule unless you really mean it. The whole point of
the bucket is "Mixpanel-style archive, never delete".
