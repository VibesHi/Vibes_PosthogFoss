#!/usr/bin/env bash
# Build, push, and deploy the Mixpanel splitter as a Cloud Run Job in europe-west4.
# One-shot tool: run, observe logs, delete when done.
#
# Required env (export before running):
#   GCP_PROJECT          e.g. hoolimoon
#   GCS_BUCKET           e.g. posthog-helper-bucket
#   SPLITTER_SA_EMAIL    e.g. posthog-migration@hoolimoon.iam.gserviceaccount.com
#                        (must have storage.objectAdmin on $GCS_BUCKET — needed
#                         because it both READS the monthly inputs and WRITES
#                         the daily outputs to the same bucket)
#
# Optional:
#   REGION               default europe-west4 (match the bucket location)
#   IMAGE_REPO           default gcr.io/$GCP_PROJECT/mixpanel-splitter
#   JOB_NAME             default mixpanel-splitter
#   SRC_PREFIX           default "" (bucket root)
#   DST_PREFIX           default mixpanel-daily/
#   CONCURRENCY          default 4
#   TASK_TIMEOUT         default 86400s (24h, the Cloud Run Job max)
#   CPU                  default 4
#   MEMORY               default 4Gi

set -euo pipefail

: "${GCP_PROJECT:?set GCP_PROJECT}"
: "${GCS_BUCKET:?set GCS_BUCKET}"
: "${SPLITTER_SA_EMAIL:?set SPLITTER_SA_EMAIL}"

REGION="${REGION:-europe-west4}"
IMAGE_REPO="${IMAGE_REPO:-gcr.io/$GCP_PROJECT/mixpanel-splitter}"
JOB_NAME="${JOB_NAME:-mixpanel-splitter}"
SRC_PREFIX="${SRC_PREFIX:-}"
DST_PREFIX="${DST_PREFIX:-mixpanel-daily/}"
CONCURRENCY="${CONCURRENCY:-4}"
TASK_TIMEOUT="${TASK_TIMEOUT:-86400s}"
CPU="${CPU:-4}"
MEMORY="${MEMORY:-4Gi}"

IMAGE_TAG="$(date -u +%Y%m%d-%H%M%S)"
IMAGE_URI="$IMAGE_REPO:$IMAGE_TAG"

cd "$(dirname "$0")"

echo "[1/4] Building image $IMAGE_URI ..."
gcloud builds submit \
    --project="$GCP_PROJECT" \
    --tag="$IMAGE_URI" \
    --timeout=20m \
    .

echo "[2/4] Creating/updating Cloud Run Job '$JOB_NAME' ..."
# `deploy` is idempotent: creates if missing, replaces config + image if exists.
gcloud run jobs deploy "$JOB_NAME" \
    --project="$GCP_PROJECT" \
    --region="$REGION" \
    --image="$IMAGE_URI" \
    --service-account="$SPLITTER_SA_EMAIL" \
    --task-timeout="$TASK_TIMEOUT" \
    --cpu="$CPU" \
    --memory="$MEMORY" \
    --max-retries=0 \
    --parallelism=1 \
    --tasks=1 \
    --args="--bucket=$GCS_BUCKET,--src-prefix=$SRC_PREFIX,--dst-prefix=$DST_PREFIX,--concurrency=$CONCURRENCY"

echo "[3/4] Executing job (will block until completion or task-timeout)..."
echo "      Tail logs in another terminal: gcloud beta run jobs logs tail $JOB_NAME --region=$REGION --project=$GCP_PROJECT"
gcloud run jobs execute "$JOB_NAME" \
    --project="$GCP_PROJECT" \
    --region="$REGION" \
    --wait

echo "[4/4] Done. Outputs at: gs://$GCS_BUCKET/$DST_PREFIX"
echo "Run 'gcloud run jobs delete $JOB_NAME --region=$REGION --project=$GCP_PROJECT' to clean up when fully done."
