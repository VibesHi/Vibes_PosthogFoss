"""Backfill Person profiles for events that were ingested in personless mode.

Why this exists:
- During the historical Mixpanel migration we flip `Team.person_processing_opt_out`
  to TRUE so the ingestion-general consumer skips person creation (~3-10x
  faster). Events get a deterministic person_id = UUIDv5(team_id:distinct_id)
  but no `posthog_person` / `posthog_persondistinctid` row is written.
- Once the bulk import is done you typically want to flip personless mode back
  off and recover Person profiles for the imported users so they show up in the
  Persons tab, can be filtered/segmented by name/email, and join cleanly to
  live SDK traffic.
- The natural recovery path is "wait for the user to come back online and fire
  $identify" — but that only works for active users. For users who churned,
  this script proactively creates Person + PersonDistinctId rows from the
  events already in ClickHouse, populated with the latest known values from
  $set / Mixpanel-native person properties.

What it does:
1. Group ClickHouse `events` by distinct_id within the import window, taking
   the latest value of each whitelisted property via `argMax(prop, timestamp)`.
2. Compute the same deterministic UUIDv5 the ingestion pipeline used for those
   events (`uuidFromDistinctId(team_id, distinct_id)`).
3. For each distinct_id with NO existing Person, insert:
   - posthog_person (PG)
   - posthog_persondistinctid (PG)
   - person row in CH via KAFKA_PERSON
   - person_distinct_id2 row in CH via KAFKA_PERSON_DISTINCT_ID
4. Skip distinct_ids that already have a Person — idempotent and safe to
   re-run after partial failure.

Key invariant: because we use the SAME UUIDv5 the imported events already
reference, no `person_distinct_id_overrides` row is needed and existing event
rows in CH require no rewriting. The Persons tab "just works" after backfill.

Run from the prod host (web container has Django + DB + CH config in place):

    docker compose -f docker-compose.prod.yml exec -T \\
        -e POSTHOG_TEAM_ID=3 \\
        web python -m ee.scripts.mixpanel_import.backfill_person_profiles \\
            --since 2024-02-01 --until 2025-01-01 \\
            --batch-size 1000 \\
            --dry-run

Drop --dry-run to actually write rows.

Pre-conditions:
- Personless mode flipped OFF for the team (so the ingestion consumer doesn't
  immediately re-strip person processing if it picks up traffic mid-run):
      ./ee/scripts/mixpanel_import/personless_mode.sh off <team_id>
- Bulk Mixpanel import finished and ClickHouse `events` is at rest. You can
  run with imports still in flight, but you'll need a second pass for any
  distinct_ids that arrive after the first run.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

# All operator-facing output goes through print() — Django's structlog
# config swallows logger.info() during django.setup().


# Property keys we lift onto the Person profile, in priority order.
# Mixpanel's raw export populates these on most events. If a key has no value
# across all events for a distinct_id, it's omitted from properties.
#
# These are the same keys PostHog's own SDKs send via $set / $set_once on
# pageview events, so the resulting Person profile blends seamlessly with
# subsequent live SDK traffic.
PERSON_PROPERTY_KEYS = [
    # identity
    "$user_id",
    "$device_id",
    "email",
    "$email",
    "name",
    "$name",
    "$initial_referrer",
    "$initial_referring_domain",
    # device / os (latest seen)
    "$os",
    "$os_version",
    "$browser",
    "$browser_version",
    "$device_type",
    "$model",
    "$manufacturer",
    "$app_version_string",
    "$app_build_number",
    "$lib_version",
    # geo (Mixpanel-native, in case GeoIP transformation is off)
    "$city",
    "$region",
    "mp_country_code",
    # subscription / business
    "plan",
    "subscription_tier",
]

REQUIRED_ENV = ("POSTHOG_TEAM_ID",)


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        print(f"ERROR: missing required env var: {name}", file=sys.stderr)
        sys.exit(2)
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill Person profiles for distinct_ids whose events were "
            "ingested in personless mode."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--team-id",
        type=int,
        default=int(_env("POSTHOG_TEAM_ID")),
        help="Target PostHog team_id",
    )
    parser.add_argument(
        "--since",
        type=str,
        default=os.environ.get("BACKFILL_SINCE", "2024-01-01"),
        help="Inclusive lower bound for event.timestamp (ISO date or datetime)",
    )
    parser.add_argument(
        "--until",
        type=str,
        default=os.environ.get("BACKFILL_UNTIL", "2099-01-01"),
        help="Exclusive upper bound for event.timestamp (ISO date or datetime)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=int(os.environ.get("BACKFILL_BATCH_SIZE", "1000")),
        help="How many distinct_ids to process per PG/CH write batch",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=int(os.environ.get("BACKFILL_LIMIT", "0")),
        help="Stop after processing this many distinct_ids (0 = no limit)",
    )
    parser.add_argument(
        "--min-event-count",
        type=int,
        default=int(os.environ.get("BACKFILL_MIN_EVENTS", "1")),
        help=(
            "Skip distinct_ids with fewer than this many events. "
            "Use >1 to skip very-low-signal users / synthetic traffic."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the CH query and report what WOULD be inserted, without writing.",
    )
    return parser.parse_args()


def parse_ts(s: str) -> datetime:
    if "T" in s or " " in s:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    else:
        dt = datetime.fromisoformat(s + "T00:00:00+00:00")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def build_ch_query(team_id: int, since: datetime, until: datetime, limit: int) -> str:
    """Aggregate per-distinct-id property snapshot via argMax(prop, timestamp).

    Filters out empty distinct_ids defensively. Bound by since/until so we can
    chunk the backfill if needed (e.g. process one quarter at a time).
    """
    arg_max_clauses = ",\n    ".join(
        f"argMaxIf(JSONExtractString(properties, '{key}'), timestamp, "
        f"JSONExtractString(properties, '{key}') != '') AS {sql_safe_alias(key)}"
        for key in PERSON_PROPERTY_KEYS
    )
    limit_clause = f"LIMIT {limit}" if limit > 0 else ""
    return f"""
        SELECT
            distinct_id,
            {arg_max_clauses},
            count() AS event_count,
            min(timestamp) AS first_seen_at,
            max(timestamp) AS last_seen_at
        FROM events
        WHERE team_id = {team_id}
          AND timestamp >= toDateTime64('{since.strftime("%Y-%m-%d %H:%M:%S")}', 6, 'UTC')
          AND timestamp <  toDateTime64('{until.strftime("%Y-%m-%d %H:%M:%S")}', 6, 'UTC')
          AND distinct_id != ''
        GROUP BY distinct_id
        {limit_clause}
    """


def sql_safe_alias(key: str) -> str:
    """Map property names to safe SQL aliases.
    `$os_version` → `prop_os_version`, `mp_country_code` → `prop_mp_country_code`.
    """
    return "prop_" + key.lstrip("$").replace(".", "_").replace("-", "_")


def row_to_properties(row: tuple, columns: list[str]) -> dict[str, str]:
    """Take a CH result row and produce {original_key: value} for non-empty values.

    `columns` is the list of aliases in row order, matching the SELECT.
    The first column is `distinct_id`, then the property aliases, then
    `event_count`, `first_seen_at`, `last_seen_at`.
    """
    props: dict[str, str] = {}
    for i, key in enumerate(PERSON_PROPERTY_KEYS):
        # +1 because position 0 is distinct_id
        value = row[i + 1]
        if value:  # skip empty strings and None
            props[key] = value
    return props


def main() -> None:
    for var in REQUIRED_ENV:
        if not os.environ.get(var):
            print(f"ERROR: missing required env var: {var}", file=sys.stderr)
            sys.exit(2)

    args = parse_args()
    since = parse_ts(args.since)
    until = parse_ts(args.until)

    if since >= until:
        print(f"ERROR: --since ({since}) must be before --until ({until})", file=sys.stderr)
        sys.exit(2)

    # Deferred imports — Django app registry must be ready first.
    from django.db import transaction

    from posthog.clickhouse.client import sync_execute
    from posthog.models.person import Person, PersonDistinctId
    from posthog.models.person.missing_person import uuidFromDistinctId
    from posthog.models.person.util import create_person, create_person_distinct_id
    from posthog.models.team.team import Team

    try:
        team = Team.objects.get(pk=args.team_id)
    except Team.DoesNotExist:
        print(f"ERROR: team_id={args.team_id} does not exist", file=sys.stderr)
        sys.exit(3)

    print(f"team={team.id} ({team.name!r})  window=[{since.isoformat()}, {until.isoformat()})")

    query = build_ch_query(team.id, since, until, args.limit)
    if args.dry_run:
        print("=" * 72)
        print("CH query (dry run):")
        print(query)
        print("=" * 72)

    print("running ClickHouse aggregation (this may take a few minutes)...")
    rows = sync_execute(query)
    total = len(rows)
    print(f"got {total} unique distinct_ids from ClickHouse")

    if args.min_event_count > 1:
        # event_count is the third-to-last column (after distinct_id + props)
        event_count_idx = 1 + len(PERSON_PROPERTY_KEYS)
        before = total
        rows = [r for r in rows if r[event_count_idx] >= args.min_event_count]
        print(f"filtered to {len(rows)} after min_event_count={args.min_event_count} (dropped {before - len(rows)})")

    if not rows:
        print("nothing to backfill")
        return

    # Pre-compute target UUIDs and look up which already have a Person row.
    distinct_ids_in_batch_order: list[str] = [r[0] for r in rows]
    target_uuids = {distinct_id: uuidFromDistinctId(team.id, distinct_id) for distinct_id in distinct_ids_in_batch_order}
    existing_uuids: set[UUID] = set(
        Person.objects.filter(team_id=team.id, uuid__in=list(target_uuids.values())).values_list("uuid", flat=True)
    )
    new_rows = [r for r in rows if target_uuids[r[0]] not in existing_uuids]
    print(f"  {len(existing_uuids)} already have Person rows  →  skipping")
    print(f"  {len(new_rows)} need backfill")

    if args.dry_run:
        sample = new_rows[: min(5, len(new_rows))]
        print("first 5 distinct_ids that would be backfilled:")
        for r in sample:
            distinct_id = r[0]
            props = row_to_properties(r, [])
            print(f"  {distinct_id}  uuid={target_uuids[distinct_id]}  props_keys={list(props.keys())}")
        print("(dry run — no rows written)")
        return

    inserted_persons = 0
    inserted_distinct_ids = 0
    failed = 0

    for batch_start in range(0, len(new_rows), args.batch_size):
        batch = new_rows[batch_start : batch_start + args.batch_size]

        person_objs: list[Person] = []
        properties_by_uuid: dict[UUID, dict[str, str]] = {}
        for r in batch:
            distinct_id = r[0]
            uuid = target_uuids[distinct_id]
            props = row_to_properties(r, [])
            properties_by_uuid[uuid] = props
            person_objs.append(
                Person(
                    team_id=team.id,
                    uuid=uuid,
                    properties=props,
                    is_identified=True,  # imported users had stable distinct_id
                    version=0,
                )
            )

        try:
            with transaction.atomic():
                # ignore_conflicts handles a race where the same distinct_id was just
                # processed by live ingestion in parallel.
                created_persons = Person.objects.bulk_create(person_objs, ignore_conflicts=True)

                # bulk_create with ignore_conflicts may return objects without PKs
                # on some DBs. Re-fetch to get pks for the FK on PersonDistinctId.
                fetched = list(
                    Person.objects.filter(team_id=team.id, uuid__in=[p.uuid for p in person_objs]).only("id", "uuid")
                )
                person_id_by_uuid = {p.uuid: p.id for p in fetched}

                pdi_objs: list[PersonDistinctId] = []
                for r in batch:
                    distinct_id = r[0]
                    uuid = target_uuids[distinct_id]
                    pid = person_id_by_uuid.get(uuid)
                    if pid is None:
                        # Could happen if Person.objects.bulk_create silently dropped
                        # this row due to ignore_conflicts AND the existing row was
                        # deleted between fetches. Skip safely.
                        continue
                    pdi_objs.append(
                        PersonDistinctId(
                            team_id=team.id,
                            person_id=pid,
                            distinct_id=distinct_id,
                            version=0,
                        )
                    )

                PersonDistinctId.objects.bulk_create(pdi_objs, ignore_conflicts=True)

            # Outside the PG transaction: produce CH rows via Kafka. These calls are
            # idempotent thanks to ReplacingMergeTree on (team_id, uuid) for person
            # and on (team_id, distinct_id) for person_distinct_id2.
            for r in batch:
                distinct_id = r[0]
                uuid = target_uuids[distinct_id]
                props = properties_by_uuid[uuid]
                try:
                    create_person(
                        team_id=team.id,
                        uuid=str(uuid),
                        properties=props,
                        is_identified=True,
                        version=0,
                    )
                    create_person_distinct_id(
                        team_id=team.id,
                        distinct_id=distinct_id,
                        person_id=str(uuid),
                        version=0,
                    )
                except Exception as e:  # noqa: BLE001 — broad catch is fine for a backfill loop
                    failed += 1
                    print(f"WARN: CH produce failed for distinct_id={distinct_id!r}: {e}", file=sys.stderr)

            inserted_persons += len(created_persons)
            inserted_distinct_ids += len(pdi_objs)
            print(
                f"  batch {batch_start + 1}-{batch_start + len(batch)} of {len(new_rows)} "
                f"→ persons={inserted_persons} pdi={inserted_distinct_ids} failed={failed}"
            )

        except Exception as e:  # noqa: BLE001
            print(f"ERROR: batch failed at offset {batch_start}: {e}", file=sys.stderr)
            failed += len(batch)
            # Continue to next batch instead of aborting — partial progress is fine
            # because the script is idempotent.
            continue

    print("=" * 72)
    print(f"done. persons_inserted={inserted_persons}  distinct_ids_inserted={inserted_distinct_ids}  failed={failed}")
    print("re-run with the same args to retry any failed rows; existing rows are skipped.")


if __name__ == "__main__":
    # Bootstrap Django for standalone invocation.
    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "posthog.settings")
    django.setup()

    main()
