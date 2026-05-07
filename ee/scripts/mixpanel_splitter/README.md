# Mixpanel monthly → daily splitter

> **Legacy.** Use `ee/scripts/mixpanel_export/export_daily.py` instead. The
> splitter amplified duplicates badly on retries (1.7M physical rows for
> 485k unique events in one observed day). Kept in repo for reference only.

One-shot Go tool that reshards Mixpanel raw export `.jsonl` files in GCS from
monthly (`events_2024-03-01_2024-03-31.jsonl`) to daily gzipped
(`mixpanel-events/moonx/2024-03-15.jsonl.gz`).

## Why split

The PostHog `batch-import-worker` `s3_gzip` source extracts each gzipped
input object to a tempfile before reading. Monthly files (37–100 GB) blow
out worker tempdir budget, and a single huge object means single-worker
throughput. Daily files (~5–10 MB compressed each) keep worker disk
pressure tiny and unlock per-day parallelism — one job key per day.

Idempotency-aware: the worker assigns deterministic UUIDv5 to each event
based on Mixpanel's `$insert_id`, so reruns of the same daily file produce
zero ClickHouse duplicates (ReplacingMergeTree dedupe). Splitting preserves
that property — every line is copied verbatim, only its destination object
key changes.

## Cost / time

- Cloud Run Job in `europe-west4` (same region as bucket): **zero egress** —
  reads stay in-region. ~1–2 hours wall time for ~1.5 TB total at
  concurrency=4 (network-bound, not CPU-bound).
- One small image build (~20 MB pushed to GCR).

## Run it (Cloud Run Job)

Prereqs:
- `gcloud` CLI authenticated as a user with `roles/cloudbuild.builds.editor`,
  `roles/run.admin`, and `roles/iam.serviceAccountUser` on the project.
- A service account that has `storage.objectAdmin` on the bucket (needs read
  + write on the same bucket — both inputs and outputs live there). The
  existing `posthog-migration@hoolimoon.iam.gserviceaccount.com` works.

```bash
export GCP_PROJECT=hoolimoon
export GCS_BUCKET=vibes-analytics-events
export SPLITTER_SA_EMAIL=posthog-migration@hoolimoon.iam.gserviceaccount.com
./deploy.sh
```

`deploy.sh` builds the image with Cloud Build, deploys the job, executes it,
and blocks on stdout. Tail logs in another terminal:

```bash
gcloud beta run jobs logs tail mixpanel-splitter \
    --region=europe-west4 --project=$GCP_PROJECT
```

## Run it (locally, for testing)

```bash
gcloud auth application-default login
go run . \
    --bucket=vibes-analytics-events \
    --src-prefix= \
    --dst-prefix=mixpanel-events/moonx/ \
    --concurrency=2 \
    --dry-run     # remove --dry-run to actually write
```

`--dry-run` reads + parses everything, writes nothing, prints per-day
counts. Useful for confirming line counts and event distribution before
committing GCS objects.

## Flags

| Flag | Default | Notes |
|---|---|---|
| `--bucket` | (required) | GCS bucket name |
| `--src-prefix` | `""` | Object prefix for inputs (empty = bucket root) |
| `--dst-prefix` | `mixpanel-events/moonx/` | Output prefix for daily files |
| `--input-pattern` | `events_` | Substring filter (skip outputs, only process inputs) |
| `--concurrency` | `4` | Number of input objects processed in parallel |
| `--max-line-bytes` | `4 MiB` | Max single JSONL line size; bump if you see "token too long" |
| `--overwrite` | `false` | Reserved for future use; current build always overwrites outputs |
| `--dry-run` | `false` | Read + parse, no GCS writes |

## Output object naming

```
gs://<bucket>/<dst-prefix>YYYY-MM-DD.jsonl.gz
```

Concrete example with defaults:
```
gs://vibes-analytics-events/mixpanel-events/moonx/2024-03-15.jsonl.gz
```

## Boundary leak

Mixpanel's monthly `from_date`/`to_date` ranges are inclusive and timezones
can leak — e.g. `events_2024-03-01_2024-03-31.jsonl` may contain a few
events with a UTC timestamp on 2024-02-29 or 2024-04-01. The splitter
classifies by **actual UTC date**, so those events land in the neighbour
file. If the neighbour month was already processed, the splitter blindly
overwrites that day's output. To avoid losing data:
- Process monthly files in chronological order, OR
- Run with `--dry-run` first and confirm the leak is small (<0.1 %).

## What gets skipped

- Lines with no `properties.time`: counted as `malformed`, dropped.
- Lines where `properties.time` isn't a number: counted as `malformed`,
  dropped.
- Outputs (any object under `--dst-prefix`) are never re-read as inputs.

The PostHog batch-import-worker handles the rest: missing `distinct_id`,
`$insert_id`-based dedup UUIDs, geo-prop translation, etc.

## Cleanup when done

```bash
gcloud run jobs delete mixpanel-splitter \
    --region=europe-west4 --project=$GCP_PROJECT
gcloud container images delete gcr.io/$GCP_PROJECT/mixpanel-splitter \
    --force-delete-tags --quiet
# Optional: delete original monthly inputs to save GCS storage
# gsutil -m rm gs://vibes-analytics-events/events_*.jsonl
```
