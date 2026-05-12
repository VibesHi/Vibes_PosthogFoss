# Mixpanel exporters

Two scripts in this folder, one for each half of a Mixpanel → PostHog migration:

| Script | What it exports | Output shape |
|---|---|---|
| `export_daily.py` | **Raw events** via `/api/2.0/export` | `<output>/YYYY/MM/YYYY-MM-DD.jsonl.gz` (one file per UTC day) |
| `engage_export.py` | **User profiles** via `/api/query/engage` | `<output>/YYYY-MM-DD.jsonl.gz` (one snapshot, NOT day-partitioned) |

Both push to any `fsspec` destination (GCS / S3 / R2 / MinIO / Azure / local FS),
both share the same Mixpanel service-account auth, both feed PostHog's
batch-import-worker — but with different `content_type` settings on the
`BatchImport` row (`mixpanel` for events, `captured` for profiles).

End-to-end migration sequence: see
[`../mixpanel_import/RUNBOOK.md`](../mixpanel_import/RUNBOOK.md). Phases 1-6
cover events (use `export_daily.py`), Phase 7 covers profiles (use
`engage_export.py`).

---

# `export_daily.py` — Mixpanel raw-events daily exporter

One script that pulls one day from Mixpanel raw export and uploads a single
gzipped JSONL object per day to **any storage** supported by `fsspec`
(GCS, S3, R2, MinIO, Azure, local FS, …), organized by year/month.

```
<output>/YYYY/MM/YYYY-MM-DD.jsonl.gz
```

`<output>` is any URL `fsspec` understands:

```
gs://vibes-analytics-events/mixpanel-events/moonx/   ← default for this repo
s3://my-bucket/
file:///data/
/data/                                                ← local FS shorthand
./out/                                                ← local FS shorthand
```

Default output is `gs://vibes-analytics-events/mixpanel-events/moonx/`
(year/month subfolders are created automatically beneath it). Override with
`--output` or `MIXPANEL_EXPORT_OUTPUT` env var.

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
| Old splitter output for `2024-03-28`    |  1,695,177 |             484,800 |      1,210,377 |

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
# Whole archive (the export root):
export GCS_PREFIX='mixpanel-events/moonx/'
# Or partition by year:
export GCS_PREFIX='mixpanel-events/moonx/2024/'
# Or by month:
export GCS_PREFIX='mixpanel-events/moonx/2024/03/'
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

---

# `engage_export.py` — Mixpanel user-profiles exporter

Pulls every user profile via Mixpanel's [Engage API](https://docs.mixpanel.com/docs/export-methods#user-profile-export-via-api)
and uploads ONE gzipped JSONL snapshot. Each line is a
[Captured-format](https://github.com/PostHog/posthog/blob/master/rust/batch-import-worker/src/parse/content/captured.rs)
`$identify` event ready for direct ingestion by `batch-import-worker` —
no transformation needed PostHog-side.

Default output:

```
gs://vibes-analytics-events/mixpanel-profiles/moonx/<YYYY-MM-DD>.jsonl.gz
```

The date is an audit tag (which export?), NOT a partition. Profiles are a
snapshot, not a time series.

## Why this exists (vs `export_daily.py`)

Mixpanel raw events do NOT include `$set` / `$set_once` payloads. After
the events import (Phase 4 of the RUNBOOK), Persons in PostHog only have
properties that happened to appear on event payloads — identity fields
like `email`, `$created`, `plan`, MRR, `$last_seen`, and anything Mixpanel
SDKs set via `mixpanel.people.set()` are missing. Engage is the only
endpoint that exposes the User Profile DB.

This script closes that gap by exporting profiles as synthetic `$identify`
events. The existing `batch-import-worker` picks them up via the
`captured` content-type path; no new Rust code, no new Kafka topic.

## What it does

1. POST `https://mixpanel.com/api/query/engage?project_id=<id>` with Basic auth.
2. Paginate via `session_id` + `page`; honor `Retry-After` on 429.
3. For each profile, transform → emit one Captured-format JSONL line:
   - Top-level `event = "$identify"`, `distinct_id`, `timestamp`
   - `$set` with all mutable profile props (email, name, os, ...)
   - `$set_once` with creation-time props (`$created`, `$initial_*`)
   - GeoIP remap (`$city`→`$geoip_city_name`, etc.) to match the events parser
   - `$insert_id = "mp-profile:<distinct_id>"` → deterministic UUIDv5 →
     idempotent reruns (ReplacingMergeTree dedupes events on `(team_id, uuid)`)
   - Drops Mixpanel-internal noise (`$transactions`, `$predict_*`, `$mp_*`,
     `$ae_total_*`, `$libraries_used`, ...) — see `MP_PROFILE_PROPS_TO_DROP`
   - Filters sentinel placeholder strings (`<null>`, `<undefined>`) that
     Engage sometimes substitutes for JSON null — see `PLACEHOLDER_STRINGS`
   - Synthesizes `$name` from `$first_name`+`$last_name` or lowercase `name`
   - Back-fills `$email` from lowercase `email`, `$os` from lowercase `os`
     (common iOS/Android Mixpanel SDK output shapes)
4. Gzip on the fly, upload via fsspec with `.uploading` → rename for
   atomic publish.

## Auth

Same as `export_daily.py`:

```bash
export MIXPANEL_USERNAME='posthog-migration.xxxxxx.mp-service-account'
export MIXPANEL_PASSWORD='...'
export MIXPANEL_PROJECT_ID='3193232'
```

GCS / S3 / FS auth identical too — see the section above.

## Usage

```bash
# Pilot: 100 profiles to local FS for shape inspection
python engage_export.py --output ./out/ --limit 100

# Full snapshot to default GCS path
python engage_export.py

# Filter to a Mixpanel cohort (useful for staged rollouts)
python engage_export.py --cohort-id 1234567

# Re-export to refresh a same-day snapshot
python engage_export.py --overwrite
```

## Importing into PostHog

After verifying the dump (line count, no `"<null>"` strings, identity fields
look real), point a `BatchImport` row at the file with
`--content-type captured`:

```bash
docker compose -f docker-compose.prod.yml exec -T \
    -e POSTHOG_TEAM_ID -e EVENTS_EXPORT_GCS_BUCKET \
    -e EVENTS_EXPORT_GCS_HMAC_KEY -e EVENTS_EXPORT_GCS_HMAC_SECRET \
    -e MIXPANEL_IMPORT_GCS_PREFIX='mixpanel-profiles/moonx/<YYYY-MM-DD>.jsonl.gz' \
    -e KAFKA_SEND_RATE=500 \
    web python -m ee.scripts.mixpanel_import.create_import \
        --content-type captured
```

`KAFKA_SEND_RATE=500` is empirically right for `captured` — each `$identify`
is ~5-10× the per-event consumer cost of a plain event (PG person upsert
+ KAFKA_PERSON write + override eval). Higher rates risk Kafka lag
exceeding retention. See the full RUNBOOK Phase 7 for monitoring details.

## Pre-flight: personless mode MUST be OFF

`Team.person_processing_opt_out=True` would cause every `$identify` event
to get dropped at `ingestion-general` with
`invalid_event_when_process_person_profile_is_false`. Verify before
running the BatchImport:

```bash
./ee/scripts/mixpanel_import/personless_mode.sh status "$POSTHOG_TEAM_ID"
# want: enabled=False
```

## Backfilling `$created`

Mixpanel only populates `$created` when an SDK call path explicitly set
it. Most projects ship profiles without it. After the profile import,
run [`backfill_created_from_events.py`](../mixpanel_import/backfill_created_from_events.py)
to derive `$created` from `min(events.timestamp)` per person:

```bash
docker compose -f docker-compose.prod.yml exec -T -e POSTHOG_TEAM_ID web \
    python -m ee.scripts.mixpanel_import.backfill_created_from_events --dry-run

# Drop --dry-run when satisfied
```

Idempotent. Skips persons that already have `$created` and persons with no
events in CH.

## Stats / output

A clean run logs:

```
DONE object=gs://.../<date>.jsonl.gz engage_total=1127126 fetched=1127126 \
     written=1127126 no_distinct_id=0 pages=1129 size_bytes=271000996
```

- `engage_total` — what Mixpanel reported in the first page's `total` field
- `fetched` — lines actually retrieved from Engage
- `written` — lines emitted to the gzip stream (= fetched − no_distinct_id)
- `no_distinct_id` — profiles dropped because `$distinct_id` was empty
- `pages` — Engage paginations
- `size_bytes` — local temp file size before upload

For typical Mixpanel projects expect `written / fetched ≈ 1.0`. If
`no_distinct_id` is non-trivial, the SDK integration was emitting profiles
without identity, and they would be unusable in PostHog anyway.

## Idempotency

Re-running the export + re-importing the resulting file is safe and cheap:

- Each emitted event has `$insert_id = "mp-profile:<distinct_id>"` →
  deterministic UUIDv5 → ClickHouse ReplacingMergeTree dedupes events
  on `(team_id, uuid)`.
- Consumer-side person upsert refreshes Person row properties (latest
  `$set` wins, `$set_once` for fields the Person already has is ignored).
- No `person_distinct_id_overrides` rows created — we reuse the same UUIDs
  the original event import generated via `uuidFromDistinctId(team_id,
  distinct_id)`.

This is the monthly-refresh path: re-run `engage_export.py --overwrite`,
re-create the `BatchImport`. Profile properties update, no duplicates.
