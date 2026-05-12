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
gsutil cat gs://vibes-analytics-events/mixpanel-events/moonx/2024/03/2024-03-15.jsonl.gz \
    | gunzip | head -1 | jq .
```
Want: `{event, properties:{time, distinct_id, $insert_id, ...}}`. Already
verified for this dataset on 2026-05-05.

### 0.2 Confirm Kafka historical topic exists with the right partition count

The compose `kafka-init` service idempotently creates/grows
`events_plugin_ingestion_historical` to `KAFKA_INGESTION_PARTITIONS` (default
6) on every startup. Verify after a deploy:

```bash
docker compose -f docker-compose.prod.yml exec kafka \
    rpk topic describe events_plugin_ingestion_historical | head -10
```

Want to see `PARTITIONS 6` (or whatever `KAFKA_INGESTION_PARTITIONS` is set to
in `.env`). If you see 1 partition, the kafka-init script didn't run or
didn't include this topic — check `docker compose logs kafka-init` and
re-run `docker compose -f docker-compose.prod.yml up -d --force-recreate
kafka-init`. Without enough partitions, only one of the 4 `ingestion-general`
consumer replicas can drain the topic, and worker throughput is bottlenecked
to ~5–10k events/sec regardless of the worker's `send_rate` setting.

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

## Phase 1 — Export Mixpanel daily files to GCS

Use `../mixpanel_export/export_daily.py` (the splitter is legacy — see its
README for why). Layout: `<output>/YYYY/MM/YYYY-MM-DD.jsonl.gz`.

```bash
cd ee/scripts/mixpanel_export
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export MIXPANEL_USERNAME='posthog-migration.xxxxxx.mp-service-account'
export MIXPANEL_PASSWORD='...'
export MIXPANEL_PROJECT_ID='3193232'

# Backfill the whole archive (default output is gs://vibes-analytics-events/mixpanel-events/moonx/)
python export_daily.py --range 2024-02-01 2026-05-01 --concurrency 2
```

Wall time: depends on Mixpanel rate-limits, typically several hours per year
of data. The script honors `Retry-After` and is fully resumable on rerun
(skips destinations that already exist).

When done, verify coverage:
```bash
gsutil ls 'gs://vibes-analytics-events/mixpanel-events/moonx/**/*.jsonl.gz' | wc -l
```

Spot-check a daily file:
```bash
gsutil cat gs://vibes-analytics-events/mixpanel-events/moonx/2024/03/2024-03-15.jsonl.gz \
    | gunzip | head -1 | jq .
```
Want: `{event, properties:{time, distinct_id, $insert_id, ...}}`. The
batch-import-worker's parser normalizes geo and translates names during
import.

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

### 2.1 Enable personless mode for the team (highly recommended)

Historical Mixpanel events go through the same `ingestion-general` consumer
as live SDK traffic. By default, the consumer does full person processing
for every event (PG lookup, person upsert, person Kafka updates). On a one-
shot bulk import this is the dominant per-event CPU cost — empirically
~3–10x slower than ingestion with person processing skipped.

PostHog has a first-class flag for this: `Team.person_processing_opt_out`.
When ON, the consumer auto-injects `$process_person_profile=false` into
every event for that team and skips the entire person path. Events still
get a deterministic `person_id` (UUIDv5 from `team_id:distinct_id`) so
unique-user counts, funnels, retention all work. See
`docs/published/handbook/engineering/person-processing.md` for details.

Caveats — what you lose while it's ON:
- No `posthog_person` rows created for new distinct_ids
- No person properties (`$set`, `$set_once`, `$unset` are dropped)
- `$identify`/`$create_alias`/`$merge_dangerously`/`$groupidentify`
  events get **dropped** with `invalid_event_when_process_person_profile_is_false`
  (Mixpanel doesn't normally emit these)
- Cohorts that depend on person properties imported only via this backfill
  won't have data. Cohorts on event properties are unaffected.

What you keep:
- All event rows in ClickHouse, fully queryable
- Counting unique users, funnels, retention, lifecycle, paths
- Filter/breakdown by event properties (incl. `$os`, `$city`, etc.)
- Filter/breakdown by `person.properties.X` for properties that came on
  the event (Persons-on-Events / PoE)
- Linking on next live `$identify`: when the user comes back online and
  the SDK fires `$identify`, an override is created that retroactively
  links all their personless historical events to the new person record

Toggle helper (idempotent, prints state before/after, restarts the
consumer so the team-cache reloads immediately):

```bash
# Dry: just show current value
./ee/scripts/mixpanel_import/personless_mode.sh status "$POSTHOG_TEAM_ID"

# Turn on — DO THIS BEFORE the first job is leased so all events skip
./ee/scripts/mixpanel_import/personless_mode.sh on "$POSTHOG_TEAM_ID"
```

Verify it's working: open the migration team in the PostHog UI →
Persons. The person count should NOT grow while imports run. If it does,
team-config cache hasn't reloaded — re-run the script or restart
ingestion-general manually.

You'll turn it OFF in Phase 5 before pointing live SDKs at this instance.

### 2.2 Disable hog function transformations (incl. GeoIP)

PostHog auto-creates an enabled GeoIP transformation hog function for every
new team (`posthog/models/hog_functions/hog_function.py:304`). It runs in
the same `ingestion-general` consumer as event processing — for every
event, it spins up a hog VM, executes the transformation code, and
short-circuits if the event has no `$ip`.

The Rust Mixpanel parser doesn't emit `$ip`
(`rust/batch-import-worker/src/parse/content/amplitude.rs:434` only the
Amplitude parser does), so the GeoIP transformation is a per-event no-op
during this migration — but the hog VM invocation overhead (~0.5–2ms/event)
is still real. Disabling it gives roughly a 1.5–2x speedup on top of
personless mode.

The transformer service short-circuits before invoking any hog VM if the
team has zero enabled transformations:
`nodejs/src/cdp/hog-transformations/hog-transformer.service.ts:183`.

Toggle helper (idempotent, snapshots which IDs were enabled so `on` only
re-enables exactly those):

```bash
# Show current state (read-only)
./ee/scripts/mixpanel_import/transformations_mode.sh status "$POSTHOG_TEAM_ID"

# Disable all enabled transformations for the team — saves IDs to
# /var/tmp/posthog-mixpanel-import/team-${id}-disabled-transformations.txt
./ee/scripts/mixpanel_import/transformations_mode.sh off "$POSTHOG_TEAM_ID"
```

Do this BEFORE the first batch import job is leased so the consumer rate
applies to the whole run. You'll re-enable in Phase 5.

## Phase 3 — Pilot with one day

### 3.1 Create the BatchImport row

The script reuses `EVENTS_EXPORT_GCS_BUCKET` / `EVENTS_EXPORT_GCS_HMAC_KEY` /
`EVENTS_EXPORT_GCS_HMAC_SECRET` from `.env` (same SA, same bucket as the
posthog-events-export container). The compose `web` service already exposes
those env vars to the container, so no extra `-e` plumbing needed.

```bash
export POSTHOG_TEAM_ID=1                       # from Phase 0.3
# Pilot: smallest day in the dataset. Pick one that fits in ~30 min:
export MIXPANEL_IMPORT_GCS_PREFIX='mixpanel-events/moonx/2024/03/2024-03-01.jsonl.gz'
```

Dry-run first (prints config, creates nothing):
```bash
docker compose -f docker-compose.prod.yml exec -T \
    -e POSTHOG_TEAM_ID="$POSTHOG_TEAM_ID" \
    -e MIXPANEL_IMPORT_GCS_PREFIX="$MIXPANEL_IMPORT_GCS_PREFIX" \
    web python -m ee.scripts.mixpanel_import.create_import --dry-run
```

Then real:
```bash
docker compose -f docker-compose.prod.yml exec -T \
    -e POSTHOG_TEAM_ID="$POSTHOG_TEAM_ID" \
    -e MIXPANEL_IMPORT_GCS_PREFIX="$MIXPANEL_IMPORT_GCS_PREFIX" \
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
INFO listing keys in bucket vibes-analytics-events prefix mixpanel-events/moonx/2024/03/2024-03-01.jsonl.gz
INFO downloading key mixpanel-events/moonx/2024/03/2024-03-01.jsonl.gz
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
docker compose -f docker-compose.prod.yml exec -T \
    -e POSTHOG_TEAM_ID="$POSTHOG_TEAM_ID" \
    -e MIXPANEL_IMPORT_GCS_PREFIX='mixpanel-events/moonx/' \
    web python -m ee.scripts.mixpanel_import.create_import
```

Worker walks the prefix recursively and processes every `.jsonl.gz` it
finds, regardless of YYYY/MM directory shape. Within one job s3_gzip
extracts keys sequentially. For cross-key parallelism, create multiple
BatchImport rows partitioned by month sub-prefix:

```bash
# Per-month partitioning (each month becomes its own job, runs in parallel
# across worker replicas)
for ym in 2024/02 2024/03 2024/04 2024/05 2024/06 2024/07 2024/08 2024/09 \
          2024/10 2024/11 2024/12 \
          2025/01 2025/02 2025/03 2025/04 2025/05 2025/06 2025/07 2025/08 \
          2025/09 2025/10 2025/11 2025/12 \
          2026/01 2026/02 2026/03 2026/04 2026/05; do
    docker compose -f docker-compose.prod.yml exec -T \
        -e POSTHOG_TEAM_ID \
        -e MIXPANEL_IMPORT_GCS_PREFIX="mixpanel-events/moonx/$ym/" \
        web python -m ee.scripts.mixpanel_import.create_import
done
```
Then bump worker replica count:
```bash
docker compose -f docker-compose.prod.yml --profile migration \
    up -d --scale batch-import-worker=4 batch-import-worker
```

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
# Re-enable hog function transformations (incl. GeoIP) for the team.
# REQUIRED if you ran Phase 2.2 — otherwise live SDK events skip
# transformations forever. Reads IDs from
# /var/tmp/posthog-mixpanel-import/team-${id}-disabled-transformations.txt
# and re-enables exactly those (won't accidentally enable transforms the
# operator had disabled for unrelated reasons).
./ee/scripts/mixpanel_import/transformations_mode.sh on "$POSTHOG_TEAM_ID"

# Turn personless mode OFF before pointing live SDKs at this instance.
# REQUIRED if you enabled it in Phase 2.1 — otherwise live traffic also
# skips person processing.
./ee/scripts/mixpanel_import/personless_mode.sh off "$POSTHOG_TEAM_ID"

# Stop the worker
docker compose -f docker-compose.prod.yml --profile migration down batch-import-worker

# Delete the GCS HMAC key (whichever SA you used)
# Console: Cloud Storage → Settings → Interoperability → trash icon

# Delete the splitter Cloud Run Job
gcloud run jobs delete mixpanel-splitter \
    --region=europe-west4 --project=hoolimoon

# Keep the daily files until you're confident the import is good. Delete later:
# gsutil -m rm -r gs://vibes-analytics-events/mixpanel-events/moonx/
```

## Phase 6 — (Optional) Backfill Person profiles for personless events

Background: in Phase 2.1 you flipped `Team.person_processing_opt_out=TRUE`,
so all imported events landed without `posthog_person` rows. Each event got
a deterministic `person_id = uuidFromDistinctId(team_id:distinct_id)` in
ClickHouse — analytics work fine, but the Persons tab is empty for users
who don't return live.

Two options:
1. **Wait for live traffic.** When a user comes back online, your live SDK
   fires `$identify` with their distinct_id, the ingestion pipeline creates
   the Person record, and (because we used the SAME deterministic UUID
   during personless ingestion) every prior event automatically links to
   the new Person — no override needed. Best for active user bases.
2. **Proactively backfill from imported events.** For users who churned
   and won't return, lift their identity properties off the imported
   events directly. Run `backfill_person_profiles.py`.

The backfill script:
- Aggregates per-distinct-id property snapshots from CH `events`
  (`argMax(prop, timestamp)` for each whitelisted key)
- Computes the same `uuidFromDistinctId(team_id, distinct_id)` the
  ingestion pipeline used during personless mode
- Inserts `posthog_person` + `posthog_persondistinctid` in PG and writes
  the matching CH rows via `KAFKA_PERSON` / `KAFKA_PERSON_DISTINCT_ID`
- Idempotent: re-running skips distinct_ids that already have a Person row
- Optional `--dry-run` shows the CH query and a sample of what would be
  inserted without writing

Run from the prod host AFTER Phase 5 (personless mode flipped off):

```bash
# Dry run first — see how many distinct_ids the script would create
docker compose -f docker-compose.prod.yml exec -T \
    -e POSTHOG_TEAM_ID="$POSTHOG_TEAM_ID" web \
    python -m ee.scripts.mixpanel_import.backfill_person_profiles \
        --since 2024-02-01 --until 2026-05-01 --dry-run

# Real run — pick a sensible batch size (1000 is reasonable; bigger
# batches put more pressure on the CH person inserts)
docker compose -f docker-compose.prod.yml exec -T \
    -e POSTHOG_TEAM_ID="$POSTHOG_TEAM_ID" web \
    python -m ee.scripts.mixpanel_import.backfill_person_profiles \
        --since 2024-02-01 --until 2026-05-01 --batch-size 1000
```

Tunable knobs (see `--help`): `--min-event-count` to skip ultra-low-signal
distinct_ids; `--limit` for incremental runs; `--since`/`--until` to chunk
by quarter if the CH aggregation is slow.

Properties lifted onto the Person profile (whitelist, see
`PERSON_PROPERTY_KEYS` in the script): `email`, `$email`, `name`, `$name`,
`$user_id`, `$device_id`, `$os`, `$os_version`, `$browser`,
`$browser_version`, `$device_type`, `$app_version_string`, `$city`,
`$region`, `mp_country_code`, `plan`, `subscription_tier`, plus a few
others. Edit the list in the script if you need additional Mixpanel-native
properties.

**No `person_distinct_id_overrides` rows are created** because the script
reuses the same deterministic UUIDv5 the imported events already reference
— event rows in CH need no rewriting. This is also why this script is
purely additive and safe to run repeatedly.

## Phase 7 — Import Mixpanel User Profiles

After events are in (Phases 3-6), profiles cover the second half of the
picture: identity properties Mixpanel stored via `$set` / `$set_once` that
the events-only path can't surface (email, name, plan, etc.) and
profile-only users (created via Engage with no events).

This phase goes through the same `batch-import-worker` you already have
running, no new Rust code, no new infra. Two new scripts, one tweaked
existing one:

| Script | Purpose |
|---|---|
| `../mixpanel_export/engage_export.py` | Mixpanel Engage API → gzipped JSONL of `$identify` events on GCS |
| `create_import.py --content-type captured` | Same `BatchImport` mechanism as events, just routed to the `Captured` parser instead of `Mixpanel` |
| `backfill_created_from_events.py` | Derive `$created` per Person from `min(events.timestamp)` for users whose Mixpanel profile didn't carry it |

The deterministic `$insert_id = "mp-profile:<distinct_id>"` makes the whole
phase idempotent end-to-end. Re-run the export → re-run the import → CH
ReplacingMergeTree dedupes events, the consumer's person upsert refreshes
properties. Safe to rerun for monthly refreshes after the migration.

### 7.0 Pre-flight

- **Personless mode MUST be OFF** for the team (Phase 5 should have flipped
  this). If it's still ON, every `$identify` is dropped at
  `ingestion-general` with `invalid_event_when_process_person_profile_is_false`
  and you see nothing in CH. Verify:
  ```bash
  ./ee/scripts/mixpanel_import/personless_mode.sh status "$POSTHOG_TEAM_ID"
  # want: enabled=False
  ```
- **Hog transformations re-enabled** (also Phase 5). Not strictly required —
  the GeoIP transformation no-ops on `$identify` events lacking `$ip` — but
  leaving Phase 5 half-done risks live traffic too.
- **Events import finished.** The `$created` backfill in 7.7 reads
  `min(events.timestamp)` per `person_id`, which presumes events are at rest.
  You can run it while events are still arriving, but you'll need a second
  pass to catch users whose first event landed after the first backfill.

### 7.1 Export Mixpanel profiles via Engage API

Run from anywhere with Mixpanel creds + write access to the GCS bucket
(the export only uses the Mixpanel `username:secret` + standard fsspec
GCS auth — does not need PostHog infra access):

```bash
cd ee/scripts/mixpanel_export
source .venv/bin/activate   # the same venv you used for export_daily.py
pip install -r requirements.txt

export MIXPANEL_USERNAME='posthog-migration.xxxxxx.mp-service-account'
export MIXPANEL_PASSWORD='...'
export MIXPANEL_PROJECT_ID='3193232'

# Pilot first — 100 profiles to local FS so you can eyeball the shape
python engage_export.py --output ./out/ --limit 100
gunzip -c ./out/$(date -u +%F).jsonl.gz | head -1 | jq .

# Full snapshot to GCS — single file, NOT day-partitioned (profiles are
# a snapshot, not a time series). Default destination:
#   gs://vibes-analytics-events/mixpanel-profiles/moonx/<YYYY-MM-DD>.jsonl.gz
python engage_export.py
```

Wall time: ~30-60 min for 1.1M profiles at Mixpanel's default rate-limit
window. Re-run with `--overwrite` if you need to refresh same-day.

### 7.2 Verify the dump

Three checks. **All three must pass before triggering the BatchImport.**

```bash
# (a) Line count matches what Engage said it would
gsutil cat gs://vibes-analytics-events/mixpanel-profiles/moonx/<YYYY-MM-DD>.jsonl.gz | \
    gunzip | wc -l
# Match: log output ended with "fetched=N written=N"

# (b) NO Mixpanel sentinel placeholder strings. We hit this on Moonly —
# Engage serializes some empties as the LITERAL string "<null>" instead of
# JSON null. The exporter filters them via PLACEHOLDER_STRINGS, but verify:
gsutil cat gs://vibes-analytics-events/mixpanel-profiles/moonx/<YYYY-MM-DD>.jsonl.gz | \
    gunzip | grep -c '"<null>"'
# MUST be 0. If not, the export hit a code path the filter doesn't cover —
# extend PLACEHOLDER_STRINGS in engage_export.py and re-export.

# (c) Identity fields look real on a handful of rows
gsutil cat gs://vibes-analytics-events/mixpanel-profiles/moonx/<YYYY-MM-DD>.jsonl.gz | \
    gunzip | head -10 | \
    jq -c '{distinct_id, name: ."$set"."$name", email: ."$set"."$email", os: ."$set"."$os"}'
```

### 7.3 Distinct_id alignment check (CRITICAL — KEY GO/NO-GO)

The profile import only works if Engage's `$distinct_id` matches the
distinct_id the event-import parser used (i.e. they produce the same
`uuidFromDistinctId(team_id, distinct_id)` UUIDv5). Mixpanel's Simplified
ID Merge can collapse multiple legacy IDs into one canonical ID, and if
events were ingested under a legacy ID but Engage returns the canonical
one, the person UUIDs won't line up → profile import creates a NEW Person
disconnected from the event history.

Pick one distinct_id from the dump, check both sides match:

```bash
# Python: deterministic UUID for the profile
docker compose -f docker-compose.prod.yml exec -T web python manage.py shell -c "
from posthog.models.person.missing_person import uuidFromDistinctId
print(uuidFromDistinctId($POSTHOG_TEAM_ID, '<one-distinct_id-from-the-dump>'))
"

# CH: what person_id are this user's existing events using?
docker compose -f docker-compose.prod.yml exec clickhouse clickhouse-client --database=posthog -q "
SELECT toString(person_id), count() FROM events
WHERE team_id=$POSTHOG_TEAM_ID
  AND distinct_id='<same-distinct_id>'
GROUP BY person_id"
```

**Both UUIDs must be identical.** If not, STOP and fix the canonicalization
before proceeding — likely needs an Engage `output_properties` tweak or a
pre-import remapping pass.

### 7.4 Pilot: 100-row BatchImport

Carve a slice into a separate prefix so you can validate end-to-end before
the full 1M:

```bash
gsutil cat gs://vibes-analytics-events/mixpanel-profiles/moonx/<YYYY-MM-DD>.jsonl.gz | \
    gunzip | head -100 | gzip | \
    gsutil cp - gs://vibes-analytics-events/mixpanel-profiles/moonx-pilot/<YYYY-MM-DD>.jsonl.gz

docker compose -f docker-compose.prod.yml exec -T \
    -e POSTHOG_TEAM_ID -e EVENTS_EXPORT_GCS_BUCKET \
    -e EVENTS_EXPORT_GCS_HMAC_KEY -e EVENTS_EXPORT_GCS_HMAC_SECRET \
    -e MIXPANEL_IMPORT_GCS_PREFIX="mixpanel-profiles/moonx-pilot/<YYYY-MM-DD>.jsonl.gz" \
    -e KAFKA_SEND_RATE=500 \
    web python -m ee.scripts.mixpanel_import.create_import \
        --content-type captured
```

Watch the worker:
```bash
docker compose -f docker-compose.prod.yml logs -f --tail=50 batch-import-worker
```
Expect within ~30s: `Claimed job <uuid>` → `Fetched part chunk` → `Writing 100 events`
→ `Batch import job complete`.

Sanity-check the result before going full scale:

```bash
# CH should have ~100 $identify events from the pilot
docker compose -f docker-compose.prod.yml exec clickhouse clickhouse-client --database=posthog -q "
SELECT count() FROM events
WHERE team_id=$POSTHOG_TEAM_ID AND event='\$identify' AND properties LIKE '%mp-profile:%'"

# Pick a profile that had an email — confirm the Person row got the props
gsutil cat gs://vibes-analytics-events/mixpanel-profiles/moonx-pilot/<YYYY-MM-DD>.jsonl.gz | \
    gunzip | jq -r 'select(."$set"."$email" != null) | .distinct_id' | head -1
# Then:
docker compose -f docker-compose.prod.yml exec -T web python manage.py shell -c "
from posthog.models.person import PersonDistinctId
pdi = PersonDistinctId.objects.filter(team_id=$POSTHOG_TEAM_ID, distinct_id='<that-distinct_id>').first()
print('uuid:', pdi.person.uuid if pdi else 'MISSING')
print('props:', list((pdi.person.properties or {}).keys())[:10])
"
```

UI sanity: PostHog → Persons tab → search the distinct_id. Should show
the user with `$name` / `$email` / `$os` populated.

### 7.5 Full BatchImport

No cleanup of the pilot needed — same `$insert_id` → same UUIDv5 →
ClickHouse `ReplacingMergeTree` dedupes events on merge, the consumer's
person upsert refreshes properties idempotently.

```bash
docker compose -f docker-compose.prod.yml exec -T \
    -e POSTHOG_TEAM_ID -e EVENTS_EXPORT_GCS_BUCKET \
    -e EVENTS_EXPORT_GCS_HMAC_KEY -e EVENTS_EXPORT_GCS_HMAC_SECRET \
    -e MIXPANEL_IMPORT_GCS_PREFIX='mixpanel-profiles/moonx/<YYYY-MM-DD>.jsonl.gz' \
    -e KAFKA_SEND_RATE=500 \
    web python -m ee.scripts.mixpanel_import.create_import --content-type captured
```

Why `KAFKA_SEND_RATE=500` and not the event-import default of 1500: each
`$identify` is ~5-10× the per-event consumer cost of a plain event (PG
person upsert + KAFKA_PERSON write + override eval). At 500/s, 1.1M
profiles drain in ~37 min Kafka-side + consumer-side processing, total
~1-2h. Higher rates risk lag piling up faster than `ingestion-general`
can drain, and on a 6h-retention cluster lag that exceeds the buffer
gets purged before consumption (see Phase 4 lessons).

### 7.6 Monitor

```bash
docker compose -f docker-compose.prod.yml logs -f batch-import-worker

# Kafka lag — must drain, not climb forever
watch -n10 'docker compose -f docker-compose.prod.yml exec kafka rpk group describe clickhouse-ingestion-historical 2>&1 | grep -E "LAG|TOPIC"'

# Person count growth in PG
docker compose -f docker-compose.prod.yml exec db psql -U posthog -d posthog -c \
    "SELECT count(*) FROM posthog_person WHERE team_id=$POSTHOG_TEAM_ID"
```

If `LAG` grows unbounded → drop `KAFKA_SEND_RATE` to 250 next time. Kill
the current row (`UPDATE posthog_batchimport SET status='failed' WHERE id='<uuid>'`),
recreate with the lower rate. Idempotent.

### 7.7 Backfill `$created` from first-event timestamps

Mixpanel's `$created` is only populated for profiles whose SDK explicitly
called `mixpanel.people.set($created)`. Many real-world projects ship
profiles without it. For ANY user with at least one event in CH, the
truest "user signup time" is `min(events.timestamp) WHERE person_id = X`.

```bash
docker compose -f docker-compose.prod.yml exec -T -e POSTHOG_TEAM_ID web \
    python -m ee.scripts.mixpanel_import.backfill_created_from_events --dry-run

# Drop --dry-run when satisfied
docker compose -f docker-compose.prod.yml exec -T -e POSTHOG_TEAM_ID web \
    python -m ee.scripts.mixpanel_import.backfill_created_from_events
```

What the script does:
1. One CH `GROUP BY person_id` query → `{person_uuid: min(timestamp)}`. Cheap.
2. Pages PG persons WHERE properties lacks `$created` via `.iterator(chunk_size=1000)`.
3. For each: merge `$created = <derived ISO>` into properties, bump
   `version`, batch-update PG, emit CH person update via `create_person()`.

Idempotent. Skips persons that already have `$created` (other migration
runs, live `$identify`), skips persons with no events in CH (profile-only
users — no better signal than the snapshot's `$last_seen`, and setting
`$created = $last_seen` would be a lie).

Wall time: ~1.19M persons at batch_size 1000 = ~1200 batches, ~30-60 min.

### 7.8 Final verification

```bash
# How many profile $identify events landed (post-dedup is ≈ profile line count)
docker compose -f docker-compose.prod.yml exec clickhouse clickhouse-client --database=posthog -q "
SELECT count() FROM events
WHERE team_id=$POSTHOG_TEAM_ID AND event='\$identify' AND properties LIKE '%mp-profile:%'"

# Persons with $email (= users Mixpanel knew the email for)
docker compose -f docker-compose.prod.yml exec db psql -U posthog -d posthog -c \
    "SELECT count(*) FROM posthog_person WHERE team_id=$POSTHOG_TEAM_ID AND properties ? '\$email'"

# Persons with $created (post-backfill — should be ≈ total persons with events)
docker compose -f docker-compose.prod.yml exec db psql -U posthog -d posthog -c \
    "SELECT count(*) FROM posthog_person WHERE team_id=$POSTHOG_TEAM_ID AND properties ? '\$created'"

# Spot-check one known user end-to-end
docker compose -f docker-compose.prod.yml exec -T web python manage.py shell -c "
from posthog.models.person import PersonDistinctId
pdi = PersonDistinctId.objects.filter(team_id=$POSTHOG_TEAM_ID, distinct_id='<some-distinct_id>').first()
print('uuid:', pdi.person.uuid)
print('name:', pdi.person.properties.get('\$name'))
print('email:', pdi.person.properties.get('\$email'))
print('created:', pdi.person.properties.get('\$created'))
print('last_seen:', pdi.person.properties.get('\$last_seen'))
print('version:', pdi.person.version)
"
```

A clean run shows `version >= 2` (initial `$identify` upsert = v1, $created
backfill = v2, any live traffic = v3+) and `created` < `last_seen`.

### 7.9 Lessons learned (Moonly run, 2026-05-12)

Captured for the next migration so we don't relearn:

- **Engage returns LITERAL `"<null>"` strings** for some empties on iOS
  profiles. Filtered in `engage_export.py:PLACEHOLDER_STRINGS`. Without
  filter, ~38% of `$name` / `$email` / `$os` values landed as the literal
  text. Always run check 7.2(b) before importing.
- **Mixpanel `name`, `email`, `os` (no `$` prefix) are common** even though
  Mixpanel's reserved properties use `$`. `engage_export.py:normalize_identity_props`
  back-fills `$name`/`$email`/`$os` from these so the PostHog Persons UI
  shows display names instead of distinct_ids.
- **`$created` is missing more often than you'd expect** — Moonly had it
  on ~0% of profiles. The backfill from event `min(timestamp)` recovered
  it for 99.998% of users (19 of 1.19M had no events in CH).
- **`KAFKA_SEND_RATE=500` is the sweet spot** for `content_type=captured`
  on a default 4-replica `ingestion-general` cluster. Higher risks
  consumer lag exceeding retention.
- **`$last_seen` is preserved as a `$set` prop AND used as the event
  timestamp.** Cohort filters on "users last active in X" work. Live
  traffic does NOT update `$last_seen` automatically though — that's a
  snapshot of Mixpanel's knowledge at export time. If you re-run the
  Engage export later, `$last_seen` refreshes.
- **Naive ISO timestamps from Engage parse fine** — PostHog's
  `parse_event_timestamp` has a jiff fallback that interprets civil
  datetimes (no TZ) as UTC. No manual normalization needed.
- **The 100-row pilot is non-negotiable.** Took 5 min, would have caught
  the `<null>` string issue if we'd run it before the first 1.1M export.

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
3. **Group profiles.** Phase 7 only covers User Profiles. If the project
   uses Group Analytics, run an analogous flow with Engage's
   `data_group_id=<group_type_id>` param, emit `$groupidentify` events
   instead of `$identify`, write to a separate GCS prefix, run a second
   `BatchImport` with `content_type=captured`. `engage_export.py` does
   NOT currently support group export — would need a `--data-group-id`
   flag and a `transform_group_profile()` analog.
4. **Kafka topic auto-creation.** If `events_plugin_ingestion_historical`
   doesn't exist before Phase 4, the worker's first produce call will
   create it with broker defaults (1 partition, 6h retention). Pre-create
   with the partition count you want (Phase 0.2) for predictable parallelism.
