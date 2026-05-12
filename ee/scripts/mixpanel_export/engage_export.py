#!/usr/bin/env python3
"""Export Mixpanel User Profiles via the Engage API and upload to any
fsspec-supported destination as a single gzipped JSONL of `$identify` events
ready to be ingested through PostHog's batch-import-worker (content_type=captured).

Why this exists (separate from `export_daily.py`):
- The Mixpanel raw-events export (`/api/2.0/export`) does NOT include profile
  properties set via `$set` / `$set_once`. The PostHog Mixpanel parser also
  strips Mixpanel-internal fields from each event, so after the historical
  events import you're missing identity-only props ($email, $created, plan,
  MRR, ...) and any profile-only users (created via Engage with no events).
- This script closes that gap. One call to Engage = latest snapshot of every
  user profile. We transform each profile row into a `Captured`-format
  `$identify` event with `$set` / `$set_once` so the existing
  batch-import-worker can materialize Person rows via the canonical
  ingestion path. Zero new infra.

Output (single file, NOT day-partitioned — profiles are a snapshot, not a
time series):

    <output>/<YYYY-MM-DD>.jsonl.gz

`<output>` is anything fsspec understands. Default:

    gs://vibes-analytics-events/mixpanel-profiles/moonx/

The date is just an audit tag, not partitioning. Re-export to refresh; point
the BatchImport at the new filename. Idempotent end-to-end because each
emitted event carries `$insert_id = "mp-profile:<distinct_id>"` → the
batch-import-worker turns that into a deterministic UUIDv5, and ClickHouse
ReplacingMergeTree dedupes on rerun. Person rows still get refreshed
because consumer-side person processing runs BEFORE event-storage dedup.

Auth:
- Mixpanel: service account `username:secret` (same one used by export_daily.py).
  Env: MIXPANEL_USERNAME, MIXPANEL_PASSWORD, MIXPANEL_PROJECT_ID.
- Output: each fsspec backend has its own auth (env vars, ADC, ~/.aws, …).
  See README.md for the cheatsheet.

Usage:

    # Default: write today's snapshot to gs://vibes-analytics-events/mixpanel-profiles/moonx/
    python engage_export.py

    # Pilot: limit to 100 profiles to a local file
    python engage_export.py --output ./out/ --limit 100

    # Filter to a Mixpanel cohort (useful for staged rollouts)
    python engage_export.py --cohort-id 1234567

Pre-flight reminders before importing the resulting file:
- `Team.person_processing_opt_out` MUST be FALSE on the target team, otherwise
  every `$identify` is dropped at ingestion-general. Verify with:
      ./ee/scripts/mixpanel_import/personless_mode.sh status <team_id>
- See ../mixpanel_import/RUNBOOK.md "Phase 7" once we write it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

import fsspec
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

MIXPANEL_ENGAGE_URL = "https://mixpanel.com/api/query/engage"

log = logging.getLogger("mixpanel-engage-export")


# Mixpanel-internal noise we do NOT want surfaced as PostHog Person properties.
# Mirrors the spirit of `MP_PROPS_TO_REMOVE` in
# rust/batch-import-worker/src/parse/content/mixpanel.rs for events, adjusted
# for profile-shaped data.
MP_PROFILE_PROPS_TO_DROP: frozenset[str] = frozenset(
    {
        "$transactions",  # array of charge events — separate domain
        "$predict_grade",
        "$predict_score",
        "$mp_api_endpoint",
        "$mp_api_timestamp_ms",
        "$mp_event_count",
        "$mp_total_events",
        "$bucket",  # internal cohort bucket id
        "$ae_first_open",
        "$ae_session_length",
        "$libraries_used",
        # We move $last_seen into the event timestamp; we ALSO keep it as a
        # $set property below so cohort filters on "last active" still work.
    }
)

# Same mapping the Rust mixpanel parser applies to event payloads, so Persons
# and Events agree on geo property names. We keep the country code under
# $geoip_country_code and skip deriving $geoip_country_name in Python (avoids
# pulling in pycountry/celes for a one-shot script — PostHog UI works fine
# with the code alone).
GEOIP_PROP_MAPPINGS: dict[str, str] = {
    "$city": "$geoip_city_name",
    "$region": "$geoip_subdivision_1_name",
    "mp_country_code": "$geoip_country_code",
    "$country_code": "$geoip_country_code",
}

# Properties Mixpanel only ever sets at profile creation — model them as
# $set_once so a future live $identify (or a re-import) can't clobber them.
SET_ONCE_KEYS: frozenset[str] = frozenset(
    {
        "$created",
        "$initial_referrer",
        "$initial_referring_domain",
        "$initial_utm_source",
        "$initial_utm_medium",
        "$initial_utm_campaign",
        "$initial_utm_term",
        "$initial_utm_content",
    }
)


def make_session(username: str, password: str) -> requests.Session:
    s = requests.Session()
    s.auth = (username, password)
    # Engage rate-limits more aggressively than raw export; respect Retry-After.
    retry = Retry(
        total=10,
        backoff_factor=2.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("POST",),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    s.mount("https://", HTTPAdapter(max_retries=retry, pool_connections=2, pool_maxsize=2))
    s.headers.update({"Accept": "application/json"})
    return s


def fetch_engage_page(
    session: requests.Session,
    project_id: str,
    *,
    page: int,
    page_size: int,
    session_id: Optional[str],
    cohort_id: Optional[int],
    connect_timeout: int,
    read_timeout: int,
) -> tuple[list[dict[str, Any]], str, int]:
    """One Engage page. Returns (results, session_id, total).

    Mixpanel pagination contract: the first call gets a session_id in the
    response; pass it back on every subsequent call. `page` is 0-indexed.
    Continue while `(page + 1) * page_size < total` OR `results` is non-empty.
    """
    data: dict[str, Any] = {"page_size": page_size, "page": page}
    if session_id is not None:
        data["session_id"] = session_id
    if cohort_id is not None:
        data["filter_by_cohort"] = json.dumps({"id": cohort_id})

    resp = session.post(
        MIXPANEL_ENGAGE_URL,
        params={"project_id": project_id},
        data=data,
        timeout=(connect_timeout, read_timeout),
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Engage page={page} HTTP {resp.status_code}: {resp.text[:500]!r}"
        )

    body = resp.json()
    if body.get("status") and body["status"] != "ok":
        raise RuntimeError(f"Engage page={page} status={body['status']}: {body!r}")

    results = body.get("results", []) or []
    returned_session_id = body.get("session_id") or session_id
    total = int(body.get("total", 0))
    if returned_session_id is None:
        raise RuntimeError(f"Engage page={page} response missing session_id: {body!r}")
    return results, returned_session_id, total


# Sentinel strings Mixpanel/Engage sometimes serializes in place of JSON null.
# Observed in 2026-05-12 export on Moonly: "<null>" appears in ~30%+ of profile
# rows for $name / $email / $os when the underlying value is unset, instead of
# JSON null. We treat them as missing values across the whole transform path.
PLACEHOLDER_STRINGS: frozenset[str] = frozenset({"<null>", "<undefined>"})


def _is_missing(v: Any) -> bool:
    """True if `v` should be treated as a missing/null Person property value.

    Catches:
    - Python None
    - empty string
    - Mixpanel's literal "<null>" / "<undefined>" placeholders
    Does NOT filter 0, False, [], or {} — those are legitimate values.
    """
    if v is None:
        return True
    if isinstance(v, str):
        return v == "" or v in PLACEHOLDER_STRINGS
    return False


def _str_or_empty(v: Any) -> str:
    if not isinstance(v, str):
        return ""
    s = v.strip()
    if s in PLACEHOLDER_STRINGS:
        return ""
    return s


def normalize_identity_props(set_props: dict[str, Any], raw: dict[str, Any]) -> None:
    """Backfill canonical PostHog identity properties from non-canonical
    Mixpanel/SDK shapes. Only writes to keys that aren't already populated by
    the caller, so explicit Mixpanel `$`-prefixed properties always win.

    The fallback chain for each canonical key was chosen for what real-world
    Mixpanel projects emit:
      - $name   ← $first_name + $last_name → name
      - $email  ← email
      - $os     ← os

    PostHog Persons UI keys off these canonical names for both display
    (Persons list shows $name, falling back to distinct_id) and built-in
    breakdowns (OS facet keys off $os). Without this remap, customer apps
    that emit lowercase `name` / `email` / `os` (extremely common on iOS/
    Android Mixpanel SDK setups) end up with bare distinct_ids in the UI.

    Mutates set_props in place.
    """
    if "$name" not in set_props:
        first = _str_or_empty(raw.get("$first_name"))
        last = _str_or_empty(raw.get("$last_name"))
        full = " ".join(p for p in (first, last) if p)
        if not full:
            full = _str_or_empty(raw.get("name"))
        if full:
            set_props["$name"] = full

    if "$email" not in set_props:
        email = _str_or_empty(raw.get("email"))
        if email:
            set_props["$email"] = email

    if "$os" not in set_props:
        os_value = _str_or_empty(raw.get("os"))
        if os_value:
            set_props["$os"] = os_value


def transform_profile(profile: dict[str, Any], default_timestamp: str) -> Optional[dict[str, Any]]:
    """Convert one Engage profile row into a Captured-format $identify event.

    Returns None for unusable rows (missing distinct_id). The shape matches
    what `rust/batch-import-worker/src/parse/content/captured.rs` expects:
    a RawEvent with top-level $set / $set_once. The worker serializes the
    whole event back to JSON and ships it to Kafka.
    """
    distinct_id = profile.get("$distinct_id")
    if distinct_id is None or distinct_id == "":
        return None
    distinct_id = str(distinct_id)

    raw = profile.get("$properties", {}) or {}
    if not isinstance(raw, dict):
        return None

    set_props: dict[str, Any] = {}
    set_once_props: dict[str, Any] = {}

    # GeoIP remap first so we don't double-emit (raw + remapped).
    for src, dst in GEOIP_PROP_MAPPINGS.items():
        if src in raw and not _is_missing(raw[src]):
            set_props[dst] = raw[src]

    for key, value in raw.items():
        if key in MP_PROFILE_PROPS_TO_DROP:
            continue
        if key in GEOIP_PROP_MAPPINGS:
            continue  # already moved above
        if _is_missing(value):
            continue
        if key in SET_ONCE_KEYS:
            set_once_props[key] = value
        else:
            set_props[key] = value

    normalize_identity_props(set_props, raw)

    timestamp = (
        raw.get("$last_seen")
        or raw.get("$created")
        or default_timestamp
    )
    if not isinstance(timestamp, str):
        timestamp = default_timestamp

    event: dict[str, Any] = {
        "event": "$identify",
        "distinct_id": distinct_id,
        "timestamp": timestamp,
        "properties": {
            "$insert_id": f"mp-profile:{distinct_id}",
            "historical_migration": True,
            "analytics_source": "mixpanel_profile",
        },
    }
    if set_props:
        event["$set"] = set_props
    if set_once_props:
        event["$set_once"] = set_once_props
    return event


def export_profiles(
    session: requests.Session,
    project_id: str,
    *,
    page_size: int,
    cohort_id: Optional[int],
    limit: int,
    connect_timeout: int,
    read_timeout: int,
) -> tuple[Path, dict[str, int]]:
    """Stream all Engage pages → local gzipped JSONL of $identify events.
    Returns (path, stats). Caller deletes path after upload."""
    fd, path_str = tempfile.mkstemp(prefix="mp-profiles-", suffix=".jsonl.gz")
    os.close(fd)
    path = Path(path_str)

    n_total = 0
    n_written = 0
    n_no_distinct_id = 0
    n_pages = 0

    default_ts = dt.datetime.now(dt.timezone.utc).isoformat()
    session_id: Optional[str] = None
    total: Optional[int] = None

    try:
        with gzip.open(path, "wb", compresslevel=6) as out:
            page = 0
            while True:
                results, session_id, total = fetch_engage_page(
                    session,
                    project_id,
                    page=page,
                    page_size=page_size,
                    session_id=session_id,
                    cohort_id=cohort_id,
                    connect_timeout=connect_timeout,
                    read_timeout=read_timeout,
                )
                n_pages += 1
                if not results:
                    break

                for profile in results:
                    n_total += 1
                    event = transform_profile(profile, default_ts)
                    if event is None:
                        n_no_distinct_id += 1
                        continue
                    out.write(json.dumps(event, separators=(",", ":")).encode("utf-8"))
                    out.write(b"\n")
                    n_written += 1
                    if limit > 0 and n_written >= limit:
                        break

                log.info(
                    "page=%d results=%d cumulative_written=%d/%s",
                    page,
                    len(results),
                    n_written,
                    total if total else "?",
                )

                if limit > 0 and n_written >= limit:
                    log.info("hit --limit=%d, stopping", limit)
                    break
                if total and (page + 1) * page_size >= total:
                    break
                page += 1
    except BaseException:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise

    stats = {
        "engage_total": total or 0,
        "fetched": n_total,
        "written": n_written,
        "no_distinct_id": n_no_distinct_id,
        "pages": n_pages,
        "size_bytes": path.stat().st_size,
    }
    log.info("export done %s", stats)
    return path, stats


def normalize_output(url: str, today: dt.date) -> tuple[str, str]:
    """Returns (parent_root, full_target_url).
    parent_root keeps trailing slash so subsequent runs don't pile up under
    one dir. full_target_url includes the dated filename.
    """
    root = url if url.endswith("/") else url + "/"
    target = f"{root}{today:%Y-%m-%d}.jsonl.gz"
    return root, target


def upload(target_url: str, local: Path) -> None:
    """Atomic-ish upload via fsspec. Mirrors export_daily.py.upload."""
    fs, dest = fsspec.core.url_to_fs(target_url)
    parent = dest.rsplit("/", 1)[0]
    if parent and not fs.exists(parent):
        fs.makedirs(parent, exist_ok=True)

    tmp = dest + ".uploading"
    size = local.stat().st_size
    log.info("upload %s (%.2f MB)", target_url, size / (1024 * 1024))

    try:
        with open(local, "rb") as src, fs.open(tmp, "wb") as dst:
            while True:
                chunk = src.read(16 * 1024 * 1024)
                if not chunk:
                    break
                dst.write(chunk)
        if fs.exists(dest):
            fs.rm(dest)
        fs.mv(tmp, dest)
    except BaseException:
        try:
            if fs.exists(tmp):
                fs.rm(tmp)
        except Exception:
            pass
        raise


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--output",
        default=os.environ.get(
            "MIXPANEL_PROFILES_OUTPUT",
            "gs://vibes-analytics-events/mixpanel-profiles/moonx/",
        ),
        help=(
            "Destination root URL (fsspec). Final object is "
            "<output>/<YYYY-MM-DD>.jsonl.gz. Default: "
            "gs://vibes-analytics-events/mixpanel-profiles/moonx/"
        ),
    )
    p.add_argument(
        "--date",
        type=lambda s: dt.date.fromisoformat(s),
        default=None,
        help="Audit-tag date (controls filename only, NOT a partition filter). "
        "Defaults to today UTC.",
    )
    p.add_argument("--project-id", default=os.environ.get("MIXPANEL_PROJECT_ID"))
    p.add_argument("--username", default=os.environ.get("MIXPANEL_USERNAME"))
    p.add_argument("--password", default=os.environ.get("MIXPANEL_PASSWORD"))
    p.add_argument(
        "--page-size",
        type=int,
        default=1000,
        help="Engage page size. Mixpanel caps at ~5000; 1000 is a safe default.",
    )
    p.add_argument(
        "--cohort-id",
        type=int,
        default=None,
        help="Mixpanel cohort id to filter on (omit to export all profiles).",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Stop after writing this many profiles (0 = no limit). For pilots.",
    )
    p.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace destination even if it already exists (default: refuse).",
    )
    p.add_argument("--connect-timeout", type=int, default=30, help="HTTP connect timeout (s).")
    p.add_argument(
        "--read-timeout",
        type=int,
        default=300,
        help="HTTP read timeout (s) per Engage page.",
    )
    p.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))

    args = p.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if not args.project_id or not args.username or not args.password:
        p.error(
            "Mixpanel credentials missing. Pass --project-id/--username/--password "
            "or set MIXPANEL_PROJECT_ID / MIXPANEL_USERNAME / MIXPANEL_PASSWORD."
        )

    today = args.date or dt.datetime.now(dt.timezone.utc).date()
    _, target = normalize_output(args.output, today)

    fs, dest = fsspec.core.url_to_fs(target)
    if fs.exists(dest) and not args.overwrite:
        log.error(
            "destination already exists: %s (pass --overwrite to replace)", target
        )
        return 2

    log.info(
        "plan: export Mixpanel profiles → %s (cohort=%s, limit=%s, page_size=%d)",
        target,
        args.cohort_id if args.cohort_id is not None else "ALL",
        args.limit if args.limit > 0 else "ALL",
        args.page_size,
    )

    session = make_session(args.username, args.password)

    try:
        local, stats = export_profiles(
            session,
            args.project_id,
            page_size=args.page_size,
            cohort_id=args.cohort_id,
            limit=args.limit,
            connect_timeout=args.connect_timeout,
            read_timeout=args.read_timeout,
        )
    except Exception:
        log.exception("export FAILED")
        return 1

    try:
        upload(target, local)
    except Exception:
        log.exception("upload FAILED")
        return 1
    finally:
        try:
            local.unlink()
        except FileNotFoundError:
            pass

    log.info(
        "DONE object=%s engage_total=%d fetched=%d written=%d no_distinct_id=%d pages=%d size_bytes=%d",
        target,
        stats["engage_total"],
        stats["fetched"],
        stats["written"],
        stats["no_distinct_id"],
        stats["pages"],
        stats["size_bytes"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
