# Mixpanel → PostHog one-shot historical import runbook

End-to-end procedure for importing ~400M historical Mixpanel events from
GCS into this self-hosted PostHog (FOSS fork) instance.

## Architecture

```
┌──────────────────────┐    ┌───────────────────┐    ┌────────────────────────┐
│ Mixpanel raw export  │    │ Cloud Run Job     │    │ batch-import-worker    │
│ (monthly .jsonl in   │ →  │ ee/scripts/       │ →  │ (Rust, in prod compose │
│  gs://posthog-helper │    │ mixpanel_splitter │    │  with `migration`     │
│  -bucket/events_*)   │    │ — daily .jsonl.gz │    │  profile)              │
└──────────────────────┘    └───────────────────┘    └────────────────────────┘
                                                              │
                                                              ▼
                          ┌───────────────────────────────────────────────────┐
                          │ Kafka topic events_plugin_ingestion_historical    │
                          │ → ingestion-general (consumer group               │
                          │     clickhouse-ingestion-historical)              │
                          │ → ClickHouse `events` (ReplacingMergeTree)        │
                          └───────────────────────────────────────────────────┘
```

Idempotency: Mixpanel `$insert_id` → deterministic UUIDv5 → ClickHouse
ReplacingMergeTree dedupe. Rerunning any day file produces zero duplicates.

## Phase 0 — Pre-flight checks

Run from your laptop. ~10 min total.

### 0.1 Sample the format on existing monthlies

```bash
gsutil cat gs://posthog-helper-bucket/events_2024-03-01_2024-03-31.jsonl \
    | head -1 | jq .
```
Want: `{event, properties:{time, distinct_id, $insert_id, ...}}`. Already
verified for this dataset on 2026-05-05.

### 0.2 Confirm Kafka historical topic exists in your cluster

```bash
docker compose -f docker-compose.prod.yml exec kafka \
    rpk topic describe events_plugin_ingestion_historical
```
If it doesn't exist yet, capture's first historical-flagged event creates it
with default partitions. Or pre-create:
```bash
docker compose -f docker-compose.prod.yml exec kafka \
    rpk topic create events_plugin_ingestion_historical -p 6 -r 1
```

### 0.3 Note your team_id and ENCRYPTION_SALT_KEYS

```bash
# From your .env or compose host shell:
echo "ENCRYPTION_SALT_KEYS=$ENCRYPTION_SALT_KEYS"

docker compose -f docker-compose.prod.yml exec -T web python -c "
from posthog.models.team.team import Team
for t in Team.objects.values('id', 'name', 'organization__name'):
    print(t)
"
```
Pick the target team_id. The worker's `ENCRYPTION_KEYS` env (forwarded from
`ENCRYPTION_SALT_KEYS` in the compose patch) MUST match the value Django
uses, otherwise it can't decrypt `BatchImport.secrets`.

## Phase 1 — Reshard monthly → daily on GCS

See `../mixpanel_splitter/README.md` for full details.

```bash
cd ee/scripts/mixpanel_splitter
export GCP_PROJECT=hoolimoon
export GCS_BUCKET=posthog-helper-bucket
export SPLITTER_SA_EMAIL=posthog-migration@hoolimoon.iam.gserviceaccount.com
./deploy.sh
```

Wall time: ~1–2 h for ~1.5 TB at concurrency 4 in europe-west4.

When done, verify:
```bash
gsutil ls -l gs://posthog-helper-bucket/mixpanel-daily/ | head
gsutil ls gs://posthog-helper-bucket/mixpanel-daily/ | wc -l   # ~810 files
```

Spot-check a daily file:
```bash
gsutil cat gs://posthog-helper-bucket/mixpanel-daily/2024-03-15.jsonl.gz \
    | gunzip | head -1 | jq .
```
Should look identical to the monthly sample (worker's parser will normalize
during import).

## Phase 2 — Build & start batch-import-worker

```bash
docker compose -f docker-compose.prod.yml build batch-import-worker
docker compose -f docker-compose.prod.yml --profile migration up -d batch-import-worker
docker compose -f docker-compose.prod.yml logs -f batch-import-worker
```
Expected first log lines:
```
Starting up...
worker leasing jobs from posthog_batchimport
```
The worker polls Postgres every few seconds for unleased jobs. With no
jobs in the table yet, it sits idle — that's correct.

## Phase 3 — Pilot with one day

### 3.1 Create the BatchImport row

Set creds on the prod host shell (NOT in any committed file):
```bash
export GCS_HMAC_ACCESS_KEY_ID='GOOG1...'
export GCS_HMAC_SECRET_ACCESS_KEY='...'
export POSTHOG_TEAM_ID=1                       # from Phase 0.3
export GCS_BUCKET=posthog-helper-bucket
# Pilot: smallest day in the dataset. Pick one that fits in ~30 min:
export GCS_PREFIX='mixpanel-daily/2024-03-01.jsonl.gz'
```

Dry-run first (prints config, creates nothing):
```bash
docker compose -f docker-compose.prod.yml exec -T \
    -e GCS_HMAC_ACCESS_KEY_ID="$GCS_HMAC_ACCESS_KEY_ID" \
    -e GCS_HMAC_SECRET_ACCESS_KEY="$GCS_HMAC_SECRET_ACCESS_KEY" \
    -e POSTHOG_TEAM_ID="$POSTHOG_TEAM_ID" \
    -e GCS_BUCKET="$GCS_BUCKET" \
    -e GCS_PREFIX="$GCS_PREFIX" \
    web python -m ee.scripts.mixpanel_import.create_import --dry-run
```

Then real:
```bash
docker compose -f docker-compose.prod.yml exec -T \
    -e GCS_HMAC_ACCESS_KEY_ID="$GCS_HMAC_ACCESS_KEY_ID" \
    -e GCS_HMAC_SECRET_ACCESS_KEY="$GCS_HMAC_SECRET_ACCESS_KEY" \
    -e POSTHOG_TEAM_ID="$POSTHOG_TEAM_ID" \
    -e GCS_BUCKET="$GCS_BUCKET" \
    -e GCS_PREFIX="$GCS_PREFIX" \
    web python -m ee.scripts.mixpanel_import.create_import
```
Output ends with the `BatchImport.id` UUID — note it.

### 3.2 Watch the worker pick it up

```bash
docker compose -f docker-compose.prod.yml logs -f batch-import-worker
```
Expect within ~10s:
```
INFO leasing job <uuid>
INFO listing keys in bucket posthog-helper-bucket prefix mixpanel-daily/2024-03-01.jsonl.gz
INFO downloading key mixpanel-daily/2024-03-01.jsonl.gz
INFO produced N events to events_plugin_ingestion_historical
INFO job <uuid> completed
```

### 3.3 Confirm ClickHouse received them

Run queries in `verification.sql` (`docker compose exec clickhouse clickhouse-client`).
Highlights:
- Count by `properties.$import_job_id = '<uuid>'` should match GCS line count
- `historical_migration = true` on every event
- `analytics_source = 'mixpanel'`
- `$pageview` events are translated correctly (no `$mp_web_page_view`)
- Geo props live under `$geoip_*`, not Mixpanel raw names

### 3.4 Idempotency check (KEY GO/NO-GO)

Re-create the same BatchImport row (same prefix). Worker reprocesses it.
Run the count query in 3.3 again — count must be **identical**, not 2x.
If it doubles, `$insert_id` isn't being read; STOP and investigate before
running the full import.

## Phase 4 — Full run

Same script, point at the whole prefix:
```bash
export GCS_PREFIX='mixpanel-daily/'

docker compose -f docker-compose.prod.yml exec -T \
    -e GCS_HMAC_ACCESS_KEY_ID="$GCS_HMAC_ACCESS_KEY_ID" \
    -e GCS_HMAC_SECRET_ACCESS_KEY="$GCS_HMAC_SECRET_ACCESS_KEY" \
    -e POSTHOG_TEAM_ID="$POSTHOG_TEAM_ID" \
    -e GCS_BUCKET="$GCS_BUCKET" \
    -e GCS_PREFIX="$GCS_PREFIX" \
    web python -m ee.scripts.mixpanel_import.create_import
```

Worker now sees ~810 keys under that prefix. It processes them sequentially
within one job (s3_gzip extracts one key at a time). To get cross-key
parallelism, scale worker replicas — but with one job, it's a single
worker. For real parallelism, create multiple BatchImport rows partitioned
by sub-prefix:

```bash
# Quarterly partitioning example (4 jobs run in parallel across 4 worker replicas)
for q in 2024-Q1 2024-Q2 2024-Q3 2024-Q4 2025-Q1 2025-Q2 2025-Q3 2025-Q4 2026-Q1 2026-Q2; do
    GCS_PREFIX="mixpanel-daily/$q-" docker compose -f docker-compose.prod.yml exec -T \
        -e GCS_HMAC_ACCESS_KEY_ID -e GCS_HMAC_SECRET_ACCESS_KEY \
        -e POSTHOG_TEAM_ID -e GCS_BUCKET -e GCS_PREFIX \
        web python -m ee.scripts.mixpanel_import.create_import
done
```
Then bump worker replica count:
```bash
docker compose -f docker-compose.prod.yml --profile migration \
    up -d --scale batch-import-worker=4 batch-import-worker
```

(Note: file naming is YYYY-MM-DD, not YYYY-QN — adapt the prefix to match
your actual layout, e.g. `mixpanel-daily/2024-03` for "March 2024 only".
Lexicographic prefix matching is good enough.)

### 4.1 Monitoring during full run

Postgres job state:
```sql
-- Run via: docker compose exec db psql -U posthog -d posthog -c "..."
SELECT
    id, status, backoff_attempt, leased_until,
    LEFT(COALESCE(status_message, ''), 120) AS status_message,
    state->>'cursor_offset' AS cursor_offset,
    state->>'current_key' AS current_key
FROM posthog_batchimport
WHERE created_at > NOW() - INTERVAL '7 days'
ORDER BY created_at DESC;
```

Kafka consumer lag:
```bash
docker compose -f docker-compose.prod.yml exec kafka rpk group describe \
    clickhouse-ingestion-historical
```
Watch the `LAG` column. As long as it's draining (not climbing forever),
ClickHouse is keeping up.

ClickHouse insert rate:
```sql
SELECT toStartOfMinute(timestamp) AS minute, count()
FROM events
WHERE properties.$import_job_id IS NOT NULL
  AND timestamp > now() - INTERVAL 1 HOUR
GROUP BY minute ORDER BY minute DESC;
```

### 4.2 If a job fails or stalls

Worker uses exponential backoff (60s → 1h max, see config.rs:53). Check
`status_message`:
```sql
SELECT id, status, backoff_attempt, status_message
FROM posthog_batchimport WHERE status = 'failed' ORDER BY updated_at DESC;
```
Common causes:
- Kafka unhealthy → restart kafka, set `status = 'running'`, clear `lease_id`/`leased_until` to NULL
- HMAC key rotated mid-job → update `secrets` (but easier: kill the row,
  recreate with new creds; idempotent reruns are free)

Reset a stuck row:
```sql
UPDATE posthog_batchimport
SET status = 'running', lease_id = NULL, leased_until = NULL,
    backoff_attempt = 0, backoff_until = NULL
WHERE id = '<uuid>';
```

## Phase 5 — Cleanup

```bash
# Stop the worker
docker compose -f docker-compose.prod.yml --profile migration down batch-import-worker

# Delete the GCS HMAC key (whichever SA you used)
# Console: Cloud Storage → Settings → Interoperability → trash icon

# Delete the splitter Cloud Run Job
gcloud run jobs delete mixpanel-splitter \
    --region=europe-west4 --project=hoolimoon

# Optional: drop the original monthly inputs to save GCS storage
gsutil -m rm 'gs://posthog-helper-bucket/events_*.jsonl'

# Keep the daily files until you're confident the import is good. Delete later:
# gsutil -m rm -r gs://posthog-helper-bucket/mixpanel-daily/
```

## What this DOESN'T do

1. **Person merging across anon → identified.** The dataset is already 99.88%
   identified so this is moot. If it weren't, see `MIGRATION_NOTES.md`
   (Path 2a in the design discussion) — would require synthesizing
   `$identify` events from a (`$device_id`, `$user_id`) mapping pre-pass and
   importing them as a second `BatchImport` with `content_type=captured`.
2. **Custom event-name remapping.** The parser only translates
   `$mp_web_page_view → $pageview`. Mobile auto-events `$ae_session` and
   `$ae_updated` come through as-is. Treat them as custom events in PostHog
   dashboards, or patch `mixpanel.rs:map_event_names` (an upstream change
   you said you didn't want — leaving as-is).
3. **Profile/users import.** This runbook only covers events. To import
   user profiles, export Mixpanel users to CSV, transform to a series of
   `$identify` events, write to a separate GCS prefix, create a second
   BatchImport with `content_type=captured`. Profiles are typically
   millions, not 400M, so any tooling works.
4. **Kafka topic auto-creation.** If `events_plugin_ingestion_historical`
   doesn't exist before Phase 4, the worker's first produce call will
   create it with broker defaults (1 partition, 6h retention). Pre-create
   with the partition count you want (Phase 0.2) for predictable parallelism.
