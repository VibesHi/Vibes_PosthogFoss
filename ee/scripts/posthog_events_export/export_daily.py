#!/usr/bin/env python3
"""Export PostHog events from local ClickHouse per day to any fsspec-supported
destination, in the SAME Mixpanel-shaped JSONL.gz format that
`ee/scripts/mixpanel_export/export_daily.py` produces.

Why the same shape? The Rust `batch-import-worker` (see
`ee/scripts/mixpanel_import/RUNBOOK.md`) already knows how to ingest
`{event, properties:{time, distinct_id, $insert_id, ...}}` — which means
a daily archive produced by THIS script is round-trip-importable into any
PostHog instance with the same tooling, no schema translation.

Pipeline:

    posthog.sharded_events (CH HTTP, JSONEachRow stream)
        → flatten + Mixpanel-shape (time/distinct_id/$insert_id/team_id)
        → gzip
        → <output>/posthog-events/team-<id>/YYYY/MM/YYYY-MM-DD.jsonl.gz

One file per (team_id, day). Teams are auto-discovered from the events
table for the requested day(s); pass --team-ids to skip the discovery scan.

Schema mapping CH → Mixpanel-shape:

    uuid           → properties.$insert_id   (CH's per-row UUID is the
                                               natural insert_id; importer's
                                               UUIDv5 hash converges on it.)
    event          → event
    timestamp      → properties.time         (toUnixTimestamp, integer seconds)
    team_id        → properties.team_id      (so a re-import can target the
                                               right team without renaming)
    distinct_id    → properties.distinct_id
    person_id      → properties.$user_id     (omitted when zero-UUID, i.e.
                                               personless events)
    properties     → properties.*            (flattened in; the original
                                               $insert_id key, if any, is
                                               replaced by the row uuid above
                                               for stable dedup)
    elements_chain → DROPPED  (PostHog-internal autocapture detail; useless
                               outside PostHog and bloats the file ~30%)
    created_at     → DROPPED  (CH ingestion timestamp, not user event time)
    _offset        → DROPPED  (Kafka offset; only present on the kafka_events
                               ingest table, not sharded_events. Listed for
                               completeness in case the script is ever pointed
                               at the kafka table.)

ClickHouse `properties` is stored as a JSON-in-string. We unwrap exactly once
on the python side; the importer expects properties as a real object.

ReplacingMergeTree dedup: we do NOT use FINAL by default — running FINAL on
a multi-million-row daily window is 3-10× slower than a plain SELECT. The
`batch-import-worker` re-derives `$insert_id` deterministically (UUIDv5 of
distinct_id + event + timestamp + team) and ClickHouse's natural dedup on
re-import collapses any row-level duplicates this script may emit. Pass
`--final` if you need clean physical rows for direct consumption (analytics
in BigQuery, etc.).

Idempotent: skips a (team, day) destination object if it already exists.
Pass `--overwrite` to force re-export.

Default output is `gs://vibes-analytics-events/` (the bucket Coldline-by-default
keeps storage cheap and forever-retained per the parent README). Override with
`--output` or `POSTHOG_EVENTS_EXPORT_OUTPUT`.

Auth (output side, GCS specifically):
- Inside the docker container we ship: HMAC interop creds via aws-cli style
  env vars. fsspec's `gcsfs` does NOT support HMAC; for HMAC GCS use s3fs
  pointed at the GCS interop endpoint:

      AWS_ACCESS_KEY_ID=$GCS_HMAC_KEY \\
      AWS_SECRET_ACCESS_KEY=$GCS_HMAC_SECRET \\
      AWS_ENDPOINT_URL=https://storage.googleapis.com \\
      python export_daily.py --output=s3://vibes-analytics-events/ ...

  The wrapping `export-events.sh` shell script under the events-export
  container handles this rewrite automatically (gs:// → s3:// + endpoint).

- For laptop / ad-hoc use with ADC, native gs:// works:

      gcloud auth application-default login
      python export_daily.py --output=gs://vibes-analytics-events/ ...

Examples:

    # Daily cron (yesterday, default bucket, auto-discover teams)
    python export_daily.py --yesterday

    # Backfill a range, single team
    python export_daily.py --range 2024-01-01 2024-12-31 --team-ids 1

    # Local dump for testing
    python export_daily.py --output=./out/ --date 2024-03-28
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

log = logging.getLogger("posthog-events-export")

# ClickHouse zero-UUID -- emitted for personless / pre-merge events.
ZERO_UUID = "00000000-0000-0000-0000-000000000000"


# --- date helpers -----------------------------------------------------------


def daterange(start: dt.date, end: dt.date) -> Iterable[dt.date]:
    cur = start
    while cur <= end:
        yield cur
        cur += dt.timedelta(days=1)


def parse_date(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


# --- output URL helpers -----------------------------------------------------


def normalize_output(url: str) -> str:
    """Trailing-slash normalize so we can join `posthog-events/team-X/...` onto
    it whether the user gave `gs://b/p`, `gs://b/p/`, `./out`, or `/abs/path`."""
    if url.endswith("/"):
        return url
    return url + "/"


def object_path(output: str, team_id: int, day: dt.date) -> str:
    """`<output>posthog-events/team-<id>/YYYY/MM/YYYY-MM-DD.jsonl.gz`"""
    return f"{output}posthog-events/team-{team_id}/{day:%Y}/{day:%m}/{day:%Y-%m-%d}.jsonl.gz"


# --- ClickHouse client ------------------------------------------------------


class ClickHouse:
    """Tiny CH HTTP client — same auth / endpoint conventions as
    docker/backup/scripts/_lib.sh:ch_query, just streaming.

    `database` is exposed on the instance so SQL builders can FULLY-QUALIFY
    table names (e.g. `posthog.sharded_events`) — same pattern the backup
    scripts use. We DELIBERATELY avoid relying on the per-connection default
    database (X-ClickHouse-Database header / `database` URL param), because
    a misconfigured CH user with default_database='default' would silently
    fail with a `Table sharded_events doesn't exist` error mid-cron.
    Fully-qualified is bulletproof."""

    def __init__(self, host: str, port: int, user: str, password: str, database: str):
        self.url = f"http://{host}:{port}/"
        self.database = database
        self.headers = {
            "X-ClickHouse-User": user,
            "X-ClickHouse-Key": password,
        }

    def stream_query(self, sql: str, *, timeout: int = 3600) -> requests.Response:
        """POST raw SQL as the request body. CH reads the body as the query
        regardless of Content-Type. Caller iterates response.iter_lines()."""
        r = requests.post(
            self.url,
            data=sql.encode("utf-8"),
            headers=self.headers,
            stream=True,
            timeout=(30, timeout),
        )
        if r.status_code != 200:
            body = r.text[:1000]
            r.close()
            raise RuntimeError(f"ClickHouse HTTP {r.status_code}: {body!r}")
        return r

    def fetch_all(self, sql: str, *, timeout: int = 300) -> list[dict]:
        """Buffered helper for small result sets (team-id discovery)."""
        with self.stream_query(sql, timeout=timeout) as r:
            return [json.loads(line) for line in r.iter_lines() if line]


# --- queries ----------------------------------------------------------------


def discover_teams_sql(database: str, day: dt.date) -> str:
    # NB: do NOT alias `toString(team_id) AS team_id`. CH 26.3+ substitutes
    # the alias back into ORDER BY / WHERE on subsequent passes, which both
    # turns ORDER BY into a lex sort ("10" < "2") AND breaks any future
    # WHERE-on-team_id we might add. JSONEachRow quotes Int64 by default
    # (`output_format_json_quote_64bit_integers=1`), so the wire format
    # is the same string we'd have gotten from toString anyway.
    return f"""
SELECT DISTINCT team_id
FROM {database}.sharded_events
WHERE timestamp >= toDateTime('{day:%Y-%m-%d} 00:00:00', 'UTC')
  AND timestamp <  toDateTime('{day:%Y-%m-%d} 00:00:00', 'UTC') + INTERVAL 1 DAY
ORDER BY team_id
FORMAT JSONEachRow
""".strip()


def export_events_sql(database: str, team_id: int, day: dt.date, *, final: bool) -> str:
    # `FINAL` deduplicates ReplacingMergeTree rows but is 3-10× slower; off by
    # default, see module docstring.
    final_clause = "FINAL" if final else ""
    # CH 26.3+ alias-shadowing trap: aliasing `toString(team_id) AS team_id`
    # makes the optimizer rewrite `WHERE team_id = N` to `WHERE toString(team_id) = N`,
    # which fails with `Code: 386 NO_COMMON_TYPE: String vs UInt8`. Same trap
    # applies to `uuid` and `person_id` if anyone later filters on them.
    # Solution: never reuse a column name as an alias for a projected expression.
    # We use `_str` suffixes for the casted projections instead.
    return f"""
SELECT
    toString(uuid)                                                              AS uuid_str,
    event,
    properties,
    toUnixTimestamp(timestamp, 'UTC')                                           AS time,
    team_id,
    distinct_id,
    if(person_id != toUUID('{ZERO_UUID}'), toString(person_id), '')             AS person_id_str
FROM {database}.sharded_events {final_clause}
WHERE team_id = {team_id}
  AND timestamp >= toDateTime('{day:%Y-%m-%d} 00:00:00', 'UTC')
  AND timestamp <  toDateTime('{day:%Y-%m-%d} 00:00:00', 'UTC') + INTERVAL 1 DAY
ORDER BY timestamp
FORMAT JSONEachRow
""".strip()


# --- transform --------------------------------------------------------------


def to_mixpanel_shape(row: dict) -> Optional[dict]:
    """CH JSONEachRow row → {event, properties:{time, distinct_id, $insert_id, ...}}.

    Returns None for malformed rows (missing required fields). The caller
    counts these as `invalid`.

    Wire-format gotchas to know about:
      * `team_id` is Int64; CH JSONEachRow quotes 64-bit ints by default
        (`output_format_json_quote_64bit_integers=1`), so `row["team_id"]`
        is a STRING like "2", not int 2. `int(...)` handles either.
      * `uuid_str` / `person_id_str` are aliased that way to avoid
        alias-shadowing ORDER BY / WHERE in CH 26.3+ (see export_events_sql)."""
    event = row.get("event")
    distinct_id = row.get("distinct_id")
    uuid = row.get("uuid_str")
    if not event or not distinct_id or not uuid:
        return None

    raw_props = row.get("properties") or "{}"
    if isinstance(raw_props, str):
        try:
            inner = json.loads(raw_props)
        except json.JSONDecodeError:
            inner = {}
        if not isinstance(inner, dict):
            inner = {}
    elif isinstance(raw_props, dict):
        inner = raw_props
    else:
        inner = {}

    # The inner properties may carry an SDK-set `$insert_id`. We override it
    # with the CH row's `uuid` because that's the ID downstream dedup uses;
    # SDK insert_ids aren't always present and aren't always unique within
    # the CH partition.
    inner.pop("$insert_id", None)

    out_props = {
        **inner,
        "time": int(row["time"]),
        "distinct_id": distinct_id,
        "$insert_id": uuid,
        # team_id in properties (in addition to the path prefix) makes the
        # file self-describing — re-importing into a different team is a
        # `--team-id` flag flip, not a sed-the-files job.
        "team_id": int(row["team_id"]),
    }
    person_id = row.get("person_id_str") or ""
    if person_id:
        out_props["$user_id"] = person_id

    return {"event": event, "properties": out_props}


# --- streaming export -------------------------------------------------------


def export_team_day(
    ch: ClickHouse,
    team_id: int,
    day: dt.date,
    *,
    final: bool,
    read_timeout: int,
) -> tuple[Path, dict]:
    """Stream one (team, day) into a local gzipped temp file. Returns
    (path, stats). Caller must delete `path` after upload."""
    fd, path_str = tempfile.mkstemp(prefix=f"ph-team{team_id}-{day:%Y-%m-%d}-", suffix=".jsonl.gz")
    os.close(fd)
    path = Path(path_str)

    n_total = 0
    n_written = 0
    n_invalid = 0

    log.info("team=%s day=%s streaming from CH", team_id, day)
    sql = export_events_sql(ch.database, team_id, day, final=final)

    try:
        with ch.stream_query(sql, timeout=read_timeout) as r, gzip.open(
            path, "wb", compresslevel=6
        ) as out:
            for raw_line in r.iter_lines(chunk_size=1 << 20, decode_unicode=False):
                if not raw_line:
                    continue
                n_total += 1
                try:
                    row = json.loads(raw_line)
                except json.JSONDecodeError:
                    n_invalid += 1
                    continue
                shaped = to_mixpanel_shape(row)
                if shaped is None:
                    n_invalid += 1
                    continue
                # `separators` strips whitespace — saves ~10% bytes vs default.
                out.write(json.dumps(shaped, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
                out.write(b"\n")
                n_written += 1
    except BaseException:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise

    stats = {
        "total": n_total,
        "written": n_written,
        "invalid": n_invalid,
        "size_bytes": path.stat().st_size,
    }
    log.info("team=%s day=%s done %s", team_id, day, stats)
    return path, stats


# --- upload -----------------------------------------------------------------


def upload(target_url: str, local: Path) -> None:
    """Upload to fsspec target. Writes to `<dest>.uploading` then renames so
    a partial upload never appears under the canonical name. On native object
    stores rename is server-side; on local FS / MinIO without versioning it's
    copy+delete, still race-free relative to readers."""
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


def already_done(target_url: str) -> bool:
    fs, dest = fsspec.core.url_to_fs(target_url)
    return fs.exists(dest)


# --- per-team-day driver ----------------------------------------------------


def process_team_day(
    *,
    ch: ClickHouse,
    output: str,
    team_id: int,
    day: dt.date,
    overwrite: bool,
    final: bool,
    read_timeout: int,
) -> dict:
    target = object_path(output, team_id, day)
    if not overwrite and already_done(target):
        log.info("team=%s day=%s skip (%s already exists)", team_id, day, target)
        return {"team_id": team_id, "day": str(day), "status": "skipped", "object": target}

    local, stats = export_team_day(ch, team_id, day, final=final, read_timeout=read_timeout)
    try:
        # Empty days happen on multi-day backfills: discover_teams unions team_ids
        # across the whole range, but a sparse team likely had events on only a
        # handful of days. Writing a ~30-byte empty .jsonl.gz for the other 300+
        # days bloats the bucket AND breaks verify.sh's MIN_SIZE assertion. We
        # skip upload entirely in that case -- the absence of an object IS the
        # signal that the team had no events that day.
        if stats["written"] == 0:
            log.info("team=%s day=%s no events, skipping upload", team_id, day)
            return {"team_id": team_id, "day": str(day), "status": "empty", "object": target, **stats}

        upload(target, local)
    finally:
        try:
            local.unlink()
        except FileNotFoundError:
            pass
    return {"team_id": team_id, "day": str(day), "status": "uploaded", "object": target, **stats}


# --- team discovery ---------------------------------------------------------


def discover_teams(ch: ClickHouse, days: list[dt.date]) -> list[int]:
    """Union the distinct team_ids that have at least one event in the
    requested day(s). For a multi-day backfill we union across all days so
    teams with sparse traffic still get their files."""
    teams: set[int] = set()
    for d in days:
        rows = ch.fetch_all(discover_teams_sql(ch.database, d))
        for row in rows:
            teams.add(int(row["team_id"]))
    return sorted(teams)


# --- arg resolution ---------------------------------------------------------


def _resolve_days(args: argparse.Namespace) -> list[dt.date]:
    if args.yesterday:
        return [dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)]
    if args.date:
        return [args.date]
    start, end = parse_date(args.range[0]), parse_date(args.range[1])
    if end < start:
        raise SystemExit("--range END must be >= START")
    return list(daterange(start, end))


def _resolve_teams(args: argparse.Namespace, ch: ClickHouse, days: list[dt.date]) -> list[int]:
    if args.team_ids:
        return sorted({int(t.strip()) for t in args.team_ids.split(",") if t.strip()})
    log.info("auto-discovering teams from CH for %d day(s)...", len(days))
    teams = discover_teams(ch, days)
    log.info("discovered %d team(s): %s", len(teams), teams)
    return teams


# --- main -------------------------------------------------------------------


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
            "POSTHOG_EVENTS_EXPORT_OUTPUT",
            "gs://vibes-analytics-events/",
        ),
        help=(
            "Destination root URL (fsspec). Final layout is "
            "<output>posthog-events/team-<id>/YYYY/MM/YYYY-MM-DD.jsonl.gz. "
            "Override via $POSTHOG_EVENTS_EXPORT_OUTPUT or this flag. "
            "Default: gs://vibes-analytics-events/"
        ),
    )
    p.add_argument(
        "--team-ids",
        default=os.environ.get("POSTHOG_EVENTS_EXPORT_TEAM_IDS"),
        help="Comma-separated team_ids to export (skips auto-discovery).",
    )

    # CH connection -- defaults match the in-compose service.
    p.add_argument("--clickhouse-host", default=os.environ.get("CLICKHOUSE_HOST", "clickhouse"))
    p.add_argument(
        "--clickhouse-port",
        type=int,
        default=int(os.environ.get("CLICKHOUSE_PORT_HTTP", 8123)),
    )
    p.add_argument("--clickhouse-user", default=os.environ.get("CLICKHOUSE_USER", "default"))
    p.add_argument("--clickhouse-password", default=os.environ.get("CLICKHOUSE_PASSWORD"))
    p.add_argument("--clickhouse-database", default=os.environ.get("CLICKHOUSE_DATABASE", "posthog"))

    p.add_argument(
        "--overwrite",
        action="store_true",
        help="Re-export and replace destination even if it already exists.",
    )
    p.add_argument(
        "--final",
        action="store_true",
        help=(
            "Use ClickHouse FINAL to dedup ReplacingMergeTree rows at query time "
            "(3-10x slower). Off by default — re-import dedup is sufficient for "
            "round-tripping back into PostHog."
        ),
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help=(
            "(team, day) tasks fetched in parallel. Each task streams from CH; "
            "high concurrency saturates the CH HTTP server and the upload pipe. "
            "Stick to 1-2 unless you've measured."
        ),
    )
    p.add_argument(
        "--read-timeout",
        type=int,
        default=int(os.environ.get("POSTHOG_EVENTS_EXPORT_READ_TIMEOUT", 7200)),
        help="HTTP read timeout (s) per (team, day) stream. Default 2h.",
    )
    p.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))

    args = p.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if not args.clickhouse_password:
        p.error("CLICKHOUSE_PASSWORD missing. Pass --clickhouse-password or set CLICKHOUSE_PASSWORD env.")

    output = normalize_output(args.output)
    days = _resolve_days(args)

    ch = ClickHouse(
        host=args.clickhouse_host,
        port=args.clickhouse_port,
        user=args.clickhouse_user,
        password=args.clickhouse_password,
        database=args.clickhouse_database,
    )

    teams = _resolve_teams(args, ch, days)
    if not teams:
        log.warning("No teams to export. Either no events in the date range, or --team-ids was empty.")
        return 0

    tasks: list[tuple[int, dt.date]] = [(t, d) for t in teams for d in days]

    log.info(
        "plan: %d (team,day) task(s) → %s (final=%s, overwrite=%s, concurrency=%d, teams=%s, days=%s)",
        len(tasks),
        output,
        args.final,
        args.overwrite,
        args.concurrency,
        teams,
        [str(d) for d in days],
    )

    results: list[dict] = []
    failures: list[tuple[tuple[int, dt.date], BaseException]] = []

    common_kwargs = dict(
        ch=ch,
        output=output,
        overwrite=args.overwrite,
        final=args.final,
        read_timeout=args.read_timeout,
    )

    if args.concurrency <= 1:
        for team_id, d in tasks:
            try:
                results.append(process_team_day(team_id=team_id, day=d, **common_kwargs))
            except Exception as exc:
                log.exception("team=%s day=%s FAILED: %s", team_id, d, exc)
                failures.append(((team_id, d), exc))
    else:
        with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
            futs = {
                ex.submit(process_team_day, team_id=team_id, day=d, **common_kwargs): (team_id, d)
                for team_id, d in tasks
            }
            for f in as_completed(futs):
                key = futs[f]
                try:
                    results.append(f.result())
                except Exception as exc:
                    log.exception("team=%s day=%s FAILED: %s", key[0], key[1], exc)
                    failures.append((key, exc))

    skipped = sum(1 for r in results if r["status"] == "skipped")
    uploaded = sum(1 for r in results if r["status"] == "uploaded")
    empty = sum(1 for r in results if r["status"] == "empty")
    total_total = sum(int(r.get("total", 0)) for r in results)
    total_written = sum(int(r.get("written", 0)) for r in results)
    total_invalid = sum(int(r.get("invalid", 0)) for r in results)

    log.info(
        "DONE uploaded=%d skipped=%d empty=%d failed=%d tasks=%d events_total=%d events_written=%d invalid=%d",
        uploaded,
        skipped,
        empty,
        len(failures),
        len(tasks),
        total_total,
        total_written,
        total_invalid,
    )

    if failures:
        for (team_id, d), exc in failures:
            log.error("FAIL team=%s day=%s -> %s", team_id, d, exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
