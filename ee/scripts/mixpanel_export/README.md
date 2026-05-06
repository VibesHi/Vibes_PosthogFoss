# Mixpanel daily exporter

One script that pulls one day from Mixpanel raw export and uploads a single
gzipped JSONL object per day to **any storage** supported by `fsspec`
(GCS, S3, R2, MinIO, Azure, local FS, …), organized by year/month.

```
<output>/YYYY/MM/YYYY-MM-DD.jsonl.gz
```

`<output>` is any URL `fsspec` understands:

```
gs://posthog-helper-bucket/        ← default for this repo
s3://my-bucket/
file:///data/
/data/                             ← local FS shorthand
./out/                             ← local FS shorthand
```

Default output is `gs://posthog-helper-bucket/` (bucket root, year/month
subfolders are created automatically). Override with `--output` or
`MIXPANEL_EXPORT_OUTPUT` env var.

## Why this exists (vs `mixpanel_splitter`)

The older pipeline was:

```
Mixpanel monthly export → GCS → splitter Cloud Run job → daily files in GCS
```

That setup amplified duplicates badly because monthly inputs got re-processed /
re-appended into the same daily output objects across retries. Concrete
example we measured on `2024-03-28`:

| File                                     | Total rows | Unique `$insert_id` | Duplicate rows |
|------------------------------------------|-----------:|--------------------:|---------------:|
| Fresh direct export (this script)        |    492,205 |             484,800 |          7,405 |
| Old `mixpanel-daily/2024-03-28.jsonl.gz` |  1,695,177 |             484,800 |      1,210,377 |

Same unique events, but old pipeline shipped 3.5× the rows. The PostHog
batch-import-worker dedupes logically by `$insert_id` (UUIDv5), so analytics
stay correct, but you pay for that explosion in Kafka, ClickHouse storage,
ingest CPU, and `FINAL` query cost. This script collapses dupes at the
source.

## What it does

1. Calls `https://data.mixpanel.com/api/2.0/export?from_date=D&to_date=D` per day.
2. Streams the response line-by-line.
3. Drops repeats by `$insert_id` (toggle with `--no-dedup`).
4. Gzips on the fly to a local temp file.
5. Uploads to `<output>/YYYY/MM/YYYY-MM-DD.jsonl.gz` via fsspec.
6. Skips a day if the destination already exists (toggle with `--overwrite`).

Idempotent and safe to rerun.

## Install

```bash
cd ee/scripts/mixpanel_export
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Python 3.11+ recommended.

`requirements.txt` pulls `gcsfs` and `s3fs`. If you only need one backend you
can drop the other; fsspec resolves on demand. Local FS needs no extra dep.

## Auth

### Mixpanel

Service account `username:secret` (Project Settings → Service Accounts):

```bash
export MIXPANEL_USERNAME='posthog-migration.xxxxxx.mp-service-account'
export MIXPANEL_PASSWORD='...'
export MIXPANEL_PROJECT_ID='3193232'
```

### GCS (`gs://...`)

Application Default Credentials. Any of:

```bash
gcloud auth application-default login                            # laptop
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json           # generic
# Inside GCE / GKE / Cloud Run — uses workload identity automatically.
```

Required role on the bucket: `roles/storage.objectAdmin` (read for the
existence check + write for the upload).

### S3 / R2 / MinIO (`s3://...`)

Standard AWS env vars are picked up by `s3fs`/`botocore`:

```bash
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_REGION=eu-central-1
# For non-AWS S3-compatible endpoints (MinIO, R2, B2 S3-compat):
export AWS_ENDPOINT_URL=https://s3.eu-central-003.example.com
```

### Local FS

Nothing to configure. The dir is created on demand.

## Usage

### Daily cron (yesterday UTC)

```bash
python export_daily.py --yesterday
```

Crontab snippet (run at 03:30 UTC every day):

```
30 3 * * * cd /opt/Vibes_PosthogFoss/ee/scripts/mixpanel_export && \
  ./.venv/bin/python export_daily.py --yesterday \
    >> /var/log/mixpanel-export.log 2>&1
```

### Backfill a range

```bash
python export_daily.py --range 2024-02-01 2026-05-01 --concurrency 2
```

`--concurrency 2` is the practical sweet spot — Mixpanel rate-limits
aggressively. The script honors `Retry-After` on `429`s so it self-throttles.
Higher values rarely help.

### Single day

```bash
python export_daily.py --output=./out/ --date 2024-03-28
```

### Re-export a day that already exists

```bash
python export_daily.py --output=./out/ --date 2024-03-28 --overwrite
```

## Output layout

```
<output>/
├── 2024/
│   ├── 02/
│   │   ├── 2024-02-01.jsonl.gz
│   │   └── …
│   ├── 03/
│   │   ├── 2024-03-01.jsonl.gz
│   │   └── …
│   └── …
├── 2025/
│   └── …
└── 2026/
    └── …
```

Lexicographic sort = chronological order.

## Importing into PostHog

`ee/scripts/mixpanel_import/create_import.py` already creates a `BatchImport`
with a GCS prefix. To import everything from this layout:

```bash
export GCS_PREFIX=''           # whole archive (bucket root)
# or partition by year:
export GCS_PREFIX='2024/'
# or month:
export GCS_PREFIX='2024/03/'
```

The Rust batch-import-worker walks the prefix recursively and processes every
`.jsonl.gz` it finds — it does not care about the year/month directory shape.

## Docker (optional)

```bash
docker build -t mixpanel-export ee/scripts/mixpanel_export
docker run --rm \
  -e MIXPANEL_USERNAME -e MIXPANEL_PASSWORD -e MIXPANEL_PROJECT_ID \
  -v $HOME/.config/gcloud:/root/.config/gcloud:ro \
  mixpanel-export --yesterday
```

## Stats / output

Each day's run logs a stats dict like:

```
day=2024-03-28 done {'total': 492205, 'written': 484800, 'duplicates': 7405,
                     'no_insert_id': 0, 'invalid_json': 0, 'size_bytes': 51234567}
```

- `total` — lines streamed from Mixpanel
- `written` — lines actually gzipped & uploaded
- `duplicates` — dropped because `$insert_id` already seen
- `no_insert_id` — events without `$insert_id`; kept (PostHog assigns UUIDv7
  on import, so they're still unique downstream)
- `invalid_json` — malformed lines (should be 0 on a healthy day)

If `duplicates / total` is consistently 0–2 % you're on a clean export.
Higher than that usually means re-runs over the same window or upstream
ingestion problems on Mixpanel's side — investigate before re-importing.

## Skipped vs failed

- `skipped` = destination already exists, `--overwrite` not set.
- `failed` = exception during fetch/upload (logged with traceback).

The exit code is non-zero only if at least one day failed.

## Atomic publishing

Uploads write to `<dest>.uploading` first and then rename to `<dest>` on
success. On native object stores (GCS, S3) every PUT is already atomic at
object level; on backends without server-side rename (local FS, plain MinIO)
the rename is a copy+delete that still keeps partial files out of the
canonical path. On crash, the `.uploading` marker may be left behind and is
safe to delete manually.
