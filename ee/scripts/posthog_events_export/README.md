# PostHog daily events exporter

One script that pulls one day of events from local ClickHouse for each
active team and uploads a single gzipped JSONL object per (team, day) to
**any storage** supported by `fsspec` (GCS, S3, R2, MinIO, Azure, local FS,
…).

Output schema is **identical** to
[`ee/scripts/mixpanel_export/`](../mixpanel_export/) — i.e. the
Mixpanel raw-export shape that the PostHog `batch-import-worker` already
knows how to ingest. A daily archive produced here is round-trip
importable.

```text
<output>/posthog-events/team-<id>/YYYY/MM/YYYY-MM-DD.jsonl.gz
```

Default output is `gs://vibes-analytics-events/` (the Coldline-by-default
bucket configured in this fork's `README.prod.md > Events export` section).
Override with `--output` or `POSTHOG_EVENTS_EXPORT_OUTPUT`.

## Why this exists

Three reasons — only the third is novel:

1. **Off-host warm archive.** Your `docker/backup/` ZIP'd ClickHouse
   `BACKUP TO S3()` artifacts are designed for *disaster recovery*, not
   analytics. They're frozen-month .zip files that need a CH instance to
   read. This produces line-delimited JSON you can `gunzip | jq` from any
   laptop, load into BigQuery, feed to Athena, etc.
2. **Mixpanel-shaped, so it's portable.** Same
   `{event, properties:{time, distinct_id, $insert_id, ...}}` envelope as
   the Mixpanel scripts. You can re-import either set into any PostHog
   instance with the existing `batch-import-worker` runbook
   (`ee/scripts/mixpanel_import/RUNBOOK.md`).
3. **Per-team partition + forever retention.** The output prefix is
   namespaced by `team-<id>`, so multi-team installs split cleanly. The
   destination bucket is intended to be lifecycle-free (no Delete rule)
   for indefinite archive — see "Forever retention" below.

## What it does

1. Resolves the day (`--yesterday`, `--date`, or `--range`).
2. (If `--team-ids` not provided) Asks ClickHouse for the distinct
   `team_id`s with at least one event in the requested day(s).
3. For each (team, day):
   - Streams `posthog.sharded_events` over CH HTTP, filtered to that
     team & day, in `JSONEachRow` format.
   - Reshapes each row to Mixpanel-shape (see below).
   - Gzips on the fly to a local temp file.
   - Uploads to
     `<output>posthog-events/team-<id>/YYYY/MM/YYYY-MM-DD.jsonl.gz` via
     fsspec with atomic `<dest>.uploading → <dest>` rename.
4. Skips a (team, day) if its destination already exists. Override with
   `--overwrite`.

Idempotent and safe to rerun.

## Schema mapping

| ClickHouse `sharded_events` column | → | Mixpanel-shape JSON                |
|------------------------------------|---|-------------------------------------|
| `uuid`                             | → | `properties.$insert_id`            |
| `event`                            | → | `event`                            |
| `properties` (JSON-in-string)      | → | `properties.*` (flattened in)      |
| `timestamp`                        | → | `properties.time` (Unix seconds)   |
| `team_id`                          | → | `properties.team_id`               |
| `distinct_id`                      | → | `properties.distinct_id`           |
| `person_id` (if non-zero UUID)     | → | `properties.$user_id`              |
| `elements_chain`                   | × | **DROPPED** (autocapture detail)   |
| `created_at`                       | × | **DROPPED** (CH ingest timestamp)  |
| `_offset`                          | × | **DROPPED** (Kafka offset; only on the kafka_events ingest table anyway) |

The CH `properties` field is stored as a JSON-in-string — we unwrap it
exactly once. If it carries an SDK-set `$insert_id`, we override with the
CH row uuid (more stable for downstream dedup).

## ReplacingMergeTree dedup

`posthog.sharded_events` is a `ReplacingMergeTree`. Un-merged parts can
emit duplicate physical rows for the same (team_id, timestamp, event,
distinct_id) tuple. We do **not** use `FINAL` by default because it's
3-10× slower on multi-million-row daily windows.

Two paths:

- Default (no `--final`). Output may contain physical-row duplicates.
  When re-imported, `batch-import-worker` deterministically derives
  `$insert_id` (UUIDv5) and ClickHouse's natural dedup collapses them on
  the receiving side. Fine for round-trip.
- `--final`. Use when the consumer is NOT another PostHog (e.g. you're
  loading into BigQuery and want clean rows).

## Install

```bash
cd ee/scripts/posthog_events_export
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Python 3.11+ recommended.

## Auth

### ClickHouse

Same env vars the `backup` container uses:

```bash
export CLICKHOUSE_HOST=clickhouse
export CLICKHOUSE_PORT_HTTP=8123
export CLICKHOUSE_USER=default
export CLICKHOUSE_PASSWORD=...   # from .env
export CLICKHOUSE_DATABASE=posthog
```

Inside the events-export container these are wired automatically.

### GCS (`gs://...`)

Two paths supported:

1. **HMAC interop (production)** — the events-export container ships with
   `EVENTS_EXPORT_GCS_HMAC_KEY` / `EVENTS_EXPORT_GCS_HMAC_SECRET` env
   vars. fsspec's `gcsfs` does NOT speak HMAC; the container's
   `export-events.sh` wrapper rewrites `gs://bucket/...` →
   `s3://bucket/...` and points `s3fs` at the GCS interop endpoint
   (`https://storage.googleapis.com`). HMAC creds are then standard AWS
   env vars. Works transparently.

2. **ADC (laptop / interactive)** — for native `gs://` via `gcsfs`:

   ```bash
   gcloud auth application-default login
   ```

   The bucket needs `roles/storage.objectAdmin` (read for the
   exists-check, write for the upload).

### S3 / R2 / MinIO (`s3://...`)

Standard AWS env vars are picked up by `s3fs`/`botocore`:

```bash
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_REGION=us-east-1
# For non-AWS S3-compatible endpoints (MinIO, R2, B2 S3-compat, GCS interop):
export AWS_ENDPOINT_URL=https://s3.eu-central-003.example.com
```

### Local FS

Nothing to configure. The dir is created on demand.

## Usage

### Daily cron (yesterday, all active teams)

```bash
python export_daily.py --yesterday
```

Inside the container this is wired by `crontab` at 06:00 UTC.

### Backfill a range, single team

```bash
python export_daily.py --range 2024-01-01 2024-12-31 --team-ids 1
```

### Single day, multiple teams

```bash
python export_daily.py --date 2024-03-28 --team-ids 1,2,3
```

### Single day, local dump for testing

```bash
python export_daily.py --output=./out/ --date 2024-03-28 --team-ids 1
```

### Re-export a (team, day) that already exists

```bash
python export_daily.py --date 2024-03-28 --team-ids 1 --overwrite
```

### Use FINAL (deduplicated rows, slower)

```bash
python export_daily.py --date 2024-03-28 --team-ids 1 --final
```

## Output layout

```text
<output>/
└── posthog-events/
    ├── team-1/
    │   ├── 2024/
    │   │   ├── 03/
    │   │   │   ├── 2024-03-01.jsonl.gz
    │   │   │   └── …
    │   │   └── …
    │   └── 2025/…
    ├── team-2/
    │   └── …
    └── team-3/
        └── …
```

Lexicographic sort = chronological order **per team**.

## Forever retention

The default destination bucket (`gs://vibes-analytics-events/`) is set to
storage class `Coldline` at the bucket level — every uploaded object is
born Coldline. **No lifecycle.json is shipped or applied.** A lifecycle
file would only be needed if you wanted automatic transitions
(Standard→Nearline→Coldline) or deletion. We want neither.

If you ever DO want to transition deeper (e.g. Coldline → Archive at 1y)
without a Delete rule, apply this manually:

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
gcloud storage buckets update gs://vibes-analytics-events \\
    --lifecycle-file=lifecycle.json
```

(Don't add a `Delete` rule unless you really mean it. The whole point of
the bucket is "Mixpanel-style archive, never delete".)

## Stats / output

Each (team, day) run logs a stats dict like:

```text
team=1 day=2024-03-28 done {'total': 412055, 'written': 412055, 'invalid': 0,
                             'size_bytes': 47832105}
```

- `total`     — events streamed from ClickHouse
- `written`   — events actually gzipped & uploaded (=`total` minus `invalid`)
- `invalid`   — malformed rows skipped (should be 0 on a healthy day)
- `size_bytes` — final gzip size on disk

End-of-run summary:

```text
DONE uploaded=14 skipped=0 failed=0 tasks=14 events_total=5_891_402
     events_written=5_891_402 invalid=0
```

## Skipped vs failed

- `skipped` = destination already exists, `--overwrite` not set.
- `failed` = exception during fetch/upload (logged with traceback).

The exit code is non-zero only if at least one task failed.

## Atomic publishing

Uploads write to `<dest>.uploading` first and then rename to `<dest>` on
success. On native object stores (GCS, S3) every PUT is already atomic at
object level; on backends without server-side rename (local FS, plain
MinIO) the rename is a copy+delete that still keeps partial files out of
the canonical path. On crash, the `.uploading` marker may be left behind
and is safe to delete manually.

## Importing back into PostHog

Same procedure as the Mixpanel runbook. See
`ee/scripts/mixpanel_import/RUNBOOK.md`. The key prefix to point the
`BatchImport` at is per-team:

```bash
export GCS_PREFIX='posthog-events/team-1/'
```

The Rust `batch-import-worker` walks the prefix recursively and processes
every `.jsonl.gz` it finds — it doesn't care about the year/month
directory shape.
