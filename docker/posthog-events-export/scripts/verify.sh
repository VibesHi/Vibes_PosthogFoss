#!/usr/bin/env bash
#
# verify.sh -- manifest-only validation of the events-export artifacts in
# GCS.
#
# Answers "are the .jsonl.gz files for each team uploaded, sane size, and
# fresh?" without doing a full re-import. Catches the most common silent-
# failure modes:
#   * upload truncated mid-stream (size < threshold)
#   * `s3fs ls` returns nothing for a team prefix (auth broken)
#   * latest object is older than expected cadence (cron not firing)
#
# What it does NOT catch:
#   * "the .jsonl.gz is intact but rows inside are malformed". For that,
#     spot-check with `gunzip -c | jq . | head` -- this script would have
#     to download every file, which defeats the point of a fast probe.
#
# Usage:
#   verify.sh                  # check all teams found under posthog-events/
#   verify.sh 1                # only team-1
#   verify.sh 1,2,3            # specific teams
#
# Exits non-zero if ANY check fails. Suitable for CI / Uptime-Kuma scrape.
#
# Implementation: we shell out to python+fsspec instead of installing
# aws-cli into this image. fsspec/s3fs is already required by export.py,
# so verify reuses the same dep tree.

SCRIPT_NAME=verify
# shellcheck source=_lib.sh
source "$(dirname "$0")/_lib.sh"

require_gcs_creds

# Standard AWS env vars consumed by s3fs against the GCS interop endpoint
# -- same path as export-events.sh.
export AWS_ACCESS_KEY_ID="${EVENTS_EXPORT_GCS_HMAC_KEY}"
export AWS_SECRET_ACCESS_KEY="${EVENTS_EXPORT_GCS_HMAC_SECRET}"
export AWS_DEFAULT_REGION="${EVENTS_EXPORT_GCS_REGION:-auto}"
export AWS_ENDPOINT_URL="https://storage.googleapis.com"
# See export-events.sh: botocore 1.36+ checksum auto-attach breaks GCS
# interop signature validation. Mirror the workaround here so verify
# runs against the same client config.
export AWS_REQUEST_CHECKSUM_CALCULATION="when_required"
export AWS_RESPONSE_CHECKSUM_VALIDATION="when_required"

TEAM_FILTER="${1:-all}"
BUCKET="$(gcs_bucket_name)"

log "verifying gs://${BUCKET}/posthog-events/ (filter=${TEAM_FILTER})"

# Python does the listing AND the assertions; non-zero exit on violations.
# Thresholds:
#   MIN_SIZE = 200B    -- below this is a truncated upload (gzip header
#                          alone is ~20B; a 1-event jsonl.gz is ~150B+).
#   MAX_AGE  = 30h     -- 24h cron cadence + 6h slack for slow streams.
python3 - "$BUCKET" "$TEAM_FILTER" <<'PY'
import os
import re
import sys
import time

import fsspec

bucket = sys.argv[1]
team_filter = sys.argv[2]

MIN_SIZE = 200
MAX_AGE = 30 * 3600

fs = fsspec.filesystem(
    "s3",
    key=os.environ["AWS_ACCESS_KEY_ID"],
    secret=os.environ["AWS_SECRET_ACCESS_KEY"],
    client_kwargs={"endpoint_url": os.environ["AWS_ENDPOINT_URL"]},
)

prefix = f"{bucket}/posthog-events/"
try:
    entries = fs.find(prefix, detail=True)
except FileNotFoundError:
    entries = {}

if not entries:
    print(f"FAIL: no objects under {prefix} -- export may have never run, or auth broken")
    sys.exit(1)

# Path shape: <bucket>/posthog-events/team-<id>/YYYY/MM/YYYY-MM-DD.jsonl.gz
# We REQUIRE the .jsonl.gz suffix so mid-upload .uploading sentinels and
# any other stray objects don't pollute counts or sneak in as `latest`.
team_re = re.compile(r"^[^/]+/posthog-events/team-(\d+)/.*\.jsonl\.gz$")
by_team: dict[str, list[tuple[str, dict]]] = {}
for path, info in entries.items():
    m = team_re.match(path)
    if not m:
        continue
    by_team.setdefault(m.group(1), []).append((path, info))

if team_filter != "all":
    wanted = {t.strip() for t in team_filter.split(",") if t.strip()}
    missing = wanted - set(by_team)
    by_team = {k: v for k, v in by_team.items() if k in wanted}
    for m_team in sorted(missing, key=int):
        print(f"FAIL: team-{m_team} requested but no objects exist")

if not by_team:
    print(f"FAIL: no teams matched filter={team_filter}")
    sys.exit(1)

now = int(time.time())
fail = 0

for team_id in sorted(by_team, key=int):
    objs = by_team[team_id]
    objs.sort(key=lambda x: x[0])  # lex sort = chronological
    latest_path, latest_info = objs[-1]
    total_bytes = sum(int(i.get("size") or 0) for _, i in objs)
    latest_size = int(latest_info.get("size") or 0)
    mtime = latest_info.get("LastModified") or latest_info.get("mtime")
    if hasattr(mtime, "timestamp"):
        mtime_unix = int(mtime.timestamp())
    elif isinstance(mtime, (int, float)):
        mtime_unix = int(mtime)
    else:
        mtime_unix = 0
    age = now - mtime_unix if mtime_unix else None

    print(
        f"team-{team_id}: count={len(objs)} "
        f"total_bytes={total_bytes:>14_} "
        f"latest_size={latest_size:>10_} "
        f"latest={latest_path}"
    )

    if latest_size < MIN_SIZE:
        print(f"  FAIL: latest is {latest_size}B, below threshold {MIN_SIZE}B")
        fail += 1
    if age is None:
        print("  FAIL: could not parse latest object mtime")
        fail += 1
    elif age > MAX_AGE:
        print(f"  FAIL: latest is {age}s old (>{MAX_AGE}s = {MAX_AGE//3600}h)")
        fail += 1

if fail:
    print(f"VERIFY FAILED: {fail} violation(s)")
    sys.exit(1)
print("VERIFY OK")
sys.exit(0)
PY
