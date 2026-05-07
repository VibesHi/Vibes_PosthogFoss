#!/usr/bin/env python3
"""Export Mixpanel events directly per day and upload to any fsspec-supported
destination (GCS, S3, MinIO, R2, local FS, ...).

Replaces the older "monthly export → GCS splitter → daily files" pipeline
(`ee/scripts/mixpanel_splitter`). That pipeline produced massive duplicate
amplification because monthly inputs were re-processed / re-appended into
the same daily output objects on retries (observed: 1.7M physical rows for
~485k unique events on 2024-03-28).

This script is the simple shape:

    Mixpanel /api/2.0/export (per day) → in-stream $insert_id dedup → gzip → <output URL>

Output layout (year/month/day) under whatever URL you point at:

    <output>/YYYY/MM/YYYY-MM-DD.jsonl.gz

`<output>` is anything `fsspec` understands, e.g.:

    gs://vibes-analytics-events/mixpanel-events/moonx/
    s3://my-bucket/mixpanel-events/
    file:///data/mixpanel-events/
    /data/mixpanel-events/                 # local FS, sugar
    ./mixpanel-events/                     # local FS, sugar

Idempotent by default: if the destination object already exists it is
skipped. Use --overwrite to force re-export.

Auth:
- Mixpanel: service account `username:secret` via env (MIXPANEL_USERNAME,
  MIXPANEL_PASSWORD, MIXPANEL_PROJECT_ID) or matching CLI flags.
- Output: each fsspec backend has its own auth (env vars, ADC, ~/.aws, …).
  See README.md for the cheatsheet.

Default output is `gs://vibes-analytics-events/mixpanel-events/moonx/` so most
invocations are short:

    # Daily cron — exports yesterday (UTC) into the default bucket
    python export_daily.py --yesterday

    # Local dump for testing
    python export_daily.py --output=./out/ --date 2024-03-28

    # Backfill range to S3-compatible storage with a custom endpoint
    AWS_ENDPOINT_URL=https://s3.eu-central-003.example.com \\
    python export_daily.py --output=s3://my-bucket/ \\
        --range 2024-02-01 2026-05-01 --concurrency 2
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
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import fsspec
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

MIXPANEL_EXPORT_URL = "https://data.mixpanel.com/api/2.0/export"

log = logging.getLogger("mixpanel-export")


def daterange(start: dt.date, end: dt.date) -> Iterable[dt.date]:
    cur = start
    while cur <= end:
        yield cur
        cur += dt.timedelta(days=1)


def normalize_output(url: str) -> str:
    """Normalize the output target so we can join `YYYY/MM/file` onto it
    safely whether the user gave `gs://b/p`, `gs://b/p/`, `./out`, or
    `/abs/path`."""
    if url.endswith("/"):
        return url
    return url + "/"


def object_path(output: str, day: dt.date) -> str:
    """`<output>/YYYY/MM/YYYY-MM-DD.jsonl.gz`"""
    return f"{output}{day:%Y}/{day:%m}/{day:%Y-%m-%d}.jsonl.gz"


def make_session(username: str, password: str) -> requests.Session:
    s = requests.Session()
    s.auth = (username, password)
    # Mixpanel rate-limits with 429 + Retry-After under load; honor it.
    retry = Retry(
        total=8,
        backoff_factor=2.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    s.mount("https://", HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4))
    s.headers.update({"Accept": "text/plain"})
    return s


def export_day(
    session: requests.Session,
    project_id: str,
    day: dt.date,
    *,
    dedup: bool,
    connect_timeout: int,
    read_timeout: int,
) -> tuple[Path, dict]:
    """Stream a day from Mixpanel into a local gzipped temp file, deduping by
    `$insert_id` if asked. Returns (path, stats). Caller is responsible for
    deleting `path` after upload."""
    params = {
        "project_id": project_id,
        "from_date": day.isoformat(),
        "to_date": day.isoformat(),
    }
    fd, path_str = tempfile.mkstemp(prefix=f"mp-{day:%Y-%m-%d}-", suffix=".jsonl.gz")
    os.close(fd)
    path = Path(path_str)

    seen_ids: set[str] = set()
    n_total = 0
    n_written = 0
    n_dups = 0
    n_no_id = 0
    n_invalid = 0

    log.info("day=%s requesting", day)
    try:
        with session.get(
            MIXPANEL_EXPORT_URL,
            params=params,
            stream=True,
            timeout=(connect_timeout, read_timeout),
        ) as r:
            if r.status_code != 200:
                # Mixpanel sometimes also returns 200 with "Unable to authenticate request"
                # plain body — handled below by first-line JSON sanity check.
                body = r.text[:500]
                raise RuntimeError(f"day={day} HTTP {r.status_code}: {body!r}")

            with gzip.open(path, "wb", compresslevel=6) as out:
                first_line_validated = False
                for raw_line in r.iter_lines(chunk_size=1 << 20, decode_unicode=False):
                    if not raw_line:
                        continue
                    n_total += 1

                    if not first_line_validated:
                        # Cheap auth/error-page detector: real export starts with `{"event":...}`.
                        try:
                            json.loads(raw_line)
                        except Exception:
                            raise RuntimeError(
                                f"day={day} first response line is not JSON, likely auth/error page: "
                                f"{raw_line[:200]!r}"
                            )
                        first_line_validated = True

                    if not dedup:
                        out.write(raw_line)
                        out.write(b"\n")
                        n_written += 1
                        continue

                    # Cheap path: only parse to grab $insert_id, never re-serialize.
                    try:
                        obj = json.loads(raw_line)
                    except Exception:
                        n_invalid += 1
                        continue

                    iid = obj.get("properties", {}).get("$insert_id") if isinstance(obj, dict) else None
                    if not iid:
                        # Keep events that lack an $insert_id; PostHog import will assign random UUID.
                        n_no_id += 1
                        out.write(raw_line)
                        out.write(b"\n")
                        n_written += 1
                        continue

                    if iid in seen_ids:
                        n_dups += 1
                        continue
                    seen_ids.add(iid)
                    out.write(raw_line)
                    out.write(b"\n")
                    n_written += 1
    except BaseException:
        # Don't leave half-written temp files behind on error or cancellation.
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise

    stats = {
        "total": n_total,
        "written": n_written,
        "duplicates": n_dups,
        "no_insert_id": n_no_id,
        "invalid_json": n_invalid,
        "size_bytes": path.stat().st_size,
    }
    log.info("day=%s done %s", day, stats)
    return path, stats


def upload(target_url: str, local: Path) -> None:
    """Upload local temp gzip to `target_url` via fsspec.

    Streams with a 16 MiB buffer so cloud backends do efficient multipart
    uploads. We also write to a `.tmp` neighbour and then `mv` it into place
    so partial uploads never appear under the canonical name on backends
    that don't have atomic put semantics (local FS, MinIO without object
    versioning, etc.). On native object stores like GCS/S3 the upload is
    already atomic at object level, but the rename is harmless and cheap.
    """
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
        # Best-effort atomic publish. mv falls back to copy+delete on backends
        # that don't support server-side rename — still race-free relative to
        # readers that look only at the final name.
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


def already_done(target_url: str) -> bool:
    fs, dest = fsspec.core.url_to_fs(target_url)
    return fs.exists(dest)


def process_day(
    day: dt.date,
    *,
    session: requests.Session,
    output: str,
    project_id: str,
    overwrite: bool,
    dedup: bool,
    connect_timeout: int,
    read_timeout: int,
) -> dict:
    target = object_path(output, day)
    if not overwrite and already_done(target):
        log.info("day=%s skip (%s already exists)", day, target)
        return {"day": str(day), "status": "skipped", "object": target}

    local, stats = export_day(
        session,
        project_id,
        day,
        dedup=dedup,
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
    )
    try:
        upload(target, local)
    finally:
        try:
            local.unlink()
        except FileNotFoundError:
            pass

    return {"day": str(day), "status": "uploaded", "object": target, **stats}


def parse_date(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def _resolve_days(args: argparse.Namespace) -> list[dt.date]:
    if args.yesterday:
        return [dt.datetime.utcnow().date() - dt.timedelta(days=1)]
    if args.date:
        return [args.date]
    start, end = parse_date(args.range[0]), parse_date(args.range[1])
    if end < start:
        raise SystemExit("--range END must be >= START")
    return list(daterange(start, end))


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--yesterday", action="store_true", help="Export yesterday (UTC).")
    target.add_argument("--date", type=parse_date, metavar="YYYY-MM-DD", help="Export a single day.")
    target.add_argument(
        "--range",
        nargs=2,
        metavar=("START", "END"),
        help="Inclusive date range, e.g. --range 2024-02-01 2026-05-01.",
    )

    p.add_argument(
        "--output",
        default=os.environ.get(
            "MIXPANEL_EXPORT_OUTPUT",
            "gs://vibes-analytics-events/mixpanel-events/moonx/",
        ),
        help=(
            "Destination root URL (fsspec). Examples: "
            "gs://bucket/, s3://bucket/, /data/, ./out/. "
            "Final layout is <output>YYYY/MM/YYYY-MM-DD.jsonl.gz. "
            "Override via $MIXPANEL_EXPORT_OUTPUT or this flag. "
            "Default: gs://vibes-analytics-events/mixpanel-events/moonx/"
        ),
    )
    p.add_argument("--project-id", default=os.environ.get("MIXPANEL_PROJECT_ID"))
    p.add_argument("--username", default=os.environ.get("MIXPANEL_USERNAME"))
    p.add_argument("--password", default=os.environ.get("MIXPANEL_PASSWORD"))
    p.add_argument(
        "--overwrite",
        action="store_true",
        help="Re-export and replace destination even if it already exists.",
    )
    p.add_argument(
        "--no-dedup",
        dest="dedup",
        action="store_false",
        default=True,
        help="Disable per-day $insert_id dedup before upload (default: dedup ON).",
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Days fetched in parallel. Mixpanel rate-limits aggressively; 1–2 is safe.",
    )
    p.add_argument("--connect-timeout", type=int, default=30, help="HTTP connect timeout (s).")
    p.add_argument(
        "--read-timeout",
        type=int,
        default=1800,
        help="HTTP read timeout (s); large days may stream for many minutes.",
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

    output = normalize_output(args.output)
    days = _resolve_days(args)

    log.info(
        "plan: %d day(s) → %s (dedup=%s, overwrite=%s, concurrency=%d)",
        len(days),
        output,
        args.dedup,
        args.overwrite,
        args.concurrency,
    )

    session = make_session(args.username, args.password)

    results: list[dict] = []
    failures: list[tuple[dt.date, BaseException]] = []

    common_kwargs = dict(
        session=session,
        output=output,
        project_id=args.project_id,
        overwrite=args.overwrite,
        dedup=args.dedup,
        connect_timeout=args.connect_timeout,
        read_timeout=args.read_timeout,
    )

    if args.concurrency <= 1:
        for d in days:
            try:
                results.append(process_day(d, **common_kwargs))
            except Exception as exc:
                log.exception("day=%s FAILED: %s", d, exc)
                failures.append((d, exc))
    else:
        with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
            futs = {ex.submit(process_day, d, **common_kwargs): d for d in days}
            for f in as_completed(futs):
                d = futs[f]
                try:
                    results.append(f.result())
                except Exception as exc:
                    log.exception("day=%s FAILED: %s", d, exc)
                    failures.append((d, exc))

    skipped = sum(1 for r in results if r["status"] == "skipped")
    uploaded = sum(1 for r in results if r["status"] == "uploaded")
    total_total = sum(int(r.get("total", 0)) for r in results)
    total_written = sum(int(r.get("written", 0)) for r in results)
    total_dups = sum(int(r.get("duplicates", 0)) for r in results)

    log.info(
        "DONE uploaded=%d skipped=%d failed=%d days=%d events_total=%d events_written=%d duplicates_dropped=%d",
        uploaded,
        skipped,
        len(failures),
        len(days),
        total_total,
        total_written,
        total_dups,
    )

    if failures:
        log.error("%d day(s) failed: %s", len(failures), ", ".join(str(d) for d, _ in failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
