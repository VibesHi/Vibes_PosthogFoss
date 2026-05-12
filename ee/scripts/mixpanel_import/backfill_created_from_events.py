"""Backfill `$created` Person property from min(events.timestamp) in ClickHouse.

Why this exists:
- The Engage-based profile import (`../mixpanel_export/engage_export.py`) maps
  Mixpanel `$properties.$created` to PostHog's `$set_once.$created`, but
  Mixpanel only populates `$created` when an SDK call path explicitly set it.
  Many real-world projects (incl. server-side-only ingestion or older Mixpanel
  SDKs) ship profiles without it.
- For ANY user with at least one event in CH, the truest "user signup time" is
  `min(events.timestamp) WHERE person_id = X`. This script derives that and
  writes it to `posthog_person.properties['$created']` for persons that lack it.

What it does:
1. One ClickHouse aggregation: min(timestamp) per person_id for the team.
   Cheap (CH groups native on person_id).
2. Pages through PG persons missing `$created`. For each:
   - Looks up derived first-seen from the CH dict.
   - Merges $created into properties; bumps version.
3. Bulk-updates PG, then emits CH person update via `create_person`
   (ReplacingMergeTree on (team_id, uuid) with the bumped version dedupes
   correctly — readers see the merged properties immediately).

Idempotent. Persons that already have $created are skipped. Re-runnable.

Pre-conditions:
- Profile import (Phase 7) has finished — persons exist in PG/CH for the
  Mixpanel users you care about.
- Event import (Phases 3-4) finished — `events` has rows.
- Personless mode OFF (Phase 5) so live $identify traffic doesn't fight
  this script for the same uuids.

Run:
    docker compose -f docker-compose.prod.yml exec -T \\
        -e POSTHOG_TEAM_ID=2 web \\
        python -m ee.scripts.mixpanel_import.backfill_created_from_events \\
            --dry-run

Drop --dry-run to write. Tune --batch-size if PG/CH are tight.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any
from uuid import UUID

# Operator output goes through print() (Django's structlog config eats logger
# calls before our basicConfig has a chance). Same convention as the rest of
# ee/scripts/mixpanel_import.


REQUIRED_ENV = ("POSTHOG_TEAM_ID",)


def _env_or_exit(name: str) -> str:
    value = os.environ.get(name)
    if value is None:
        print(f"ERROR: missing required env var: {name}", file=sys.stderr)
        sys.exit(2)
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill $created on Person rows from min(events.timestamp) for "
            "persons that lack it. Idempotent."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--team-id",
        type=int,
        default=int(_env_or_exit("POSTHOG_TEAM_ID")),
        help="Target PostHog team_id",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=int(os.environ.get("BACKFILL_BATCH_SIZE", "1000")),
        help="Persons per PG bulk_update + CH produce cycle",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=int(os.environ.get("BACKFILL_LIMIT", "0")),
        help="Stop after updating N persons (0 = no limit). Useful for pilots.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the CH query, sample 5 candidate persons, write nothing.",
    )
    return parser.parse_args()


def build_ch_query(team_id: int) -> str:
    """Earliest event timestamp per person_id for the team. We don't bound by
    timestamp window — including live events gives a strictly more accurate
    first-seen, and CH's MergeTree primary key makes the GROUP BY cheap even
    over hundreds of millions of rows."""
    return f"""
        SELECT
            toString(person_id) AS person_uuid,
            min(timestamp) AS first_seen
        FROM events
        WHERE team_id = {team_id}
        GROUP BY person_id
    """


def main() -> None:
    args = parse_args()

    # Deferred — Django app registry must be initialized first.
    from django.db import transaction

    from posthog.clickhouse.client import sync_execute
    from posthog.models.person import Person
    from posthog.models.person.util import create_person
    from posthog.models.team.team import Team

    try:
        team = Team.objects.get(pk=args.team_id)
    except Team.DoesNotExist:
        print(f"ERROR: team_id={args.team_id} does not exist", file=sys.stderr)
        sys.exit(3)

    print(f"team={team.id} ({team.name!r})")

    query = build_ch_query(team.id)
    if args.dry_run:
        print("=" * 72)
        print("CH query (dry run):")
        print(query)
        print("=" * 72)

    print("aggregating min(timestamp) per person_id from ClickHouse...")
    rows = sync_execute(query)
    first_seen_by_uuid: dict[str, Any] = {row[0]: row[1] for row in rows}
    print(f"got {len(first_seen_by_uuid)} person_ids from ClickHouse")

    # PG persons missing $created. We use `exclude(properties__has_key=...)`
    # which compiles to a JSONB `?` operator that hits a GIN index on
    # posthog_person.properties (set up by the default Person migration).
    candidates_qs = (
        Person.objects.filter(team_id=team.id)
        .exclude(properties__has_key="$created")
        # `is_identified` must be in only() — flush_batch reads it when
        # producing the CH update. Without it Django lazy-loads per row → N+1.
        .only("id", "uuid", "version", "properties", "is_identified")
    )
    total_candidates = candidates_qs.count()
    print(f"{total_candidates} persons lack $created")

    if total_candidates == 0:
        print("nothing to do")
        return

    if args.dry_run:
        sample = list(candidates_qs[:5])
        print("first 5 candidates that would be updated:")
        for p in sample:
            ts = first_seen_by_uuid.get(str(p.uuid))
            print(
                f"  uuid={p.uuid} version={p.version} props_keys={list(p.properties.keys())[:6]} "
                f"→ would set $created = {ts}"
            )
        print("(dry run — no rows written)")
        return

    updated = 0
    no_events = 0
    failed = 0
    processed = 0

    # Iterator with chunk_size avoids loading all 1M+ persons in memory.
    batch: list[Person] = []
    for person in candidates_qs.iterator(chunk_size=args.batch_size):
        ts = first_seen_by_uuid.get(str(person.uuid))
        if ts is None:
            # Profile-only user — exists as a Person but never fired an event
            # in CH. Skip; we have nothing better than the snapshot $last_seen
            # already on the profile, and pretending it equals $created would
            # be a lie.
            no_events += 1
            continue

        person.properties["$created"] = ts.isoformat()
        person.version = (person.version or 0) + 1
        batch.append(person)

        if len(batch) >= args.batch_size:
            try:
                flush_batch(batch, team.id, create_person, transaction)
                updated += len(batch)
            except Exception as e:  # noqa: BLE001 — keep the loop alive
                failed += len(batch)
                print(f"WARN: batch flush failed at offset {processed}: {e}", file=sys.stderr)
            processed += len(batch)
            batch = []
            print(
                f"  progress: processed={processed} updated={updated} "
                f"no_events={no_events} failed={failed}"
            )
            if args.limit > 0 and updated >= args.limit:
                print(f"hit --limit={args.limit}, stopping")
                break

    # Flush trailing partial batch
    if batch and (args.limit == 0 or updated < args.limit):
        try:
            flush_batch(batch, team.id, create_person, transaction)
            updated += len(batch)
        except Exception as e:  # noqa: BLE001
            failed += len(batch)
            print(f"WARN: final batch flush failed: {e}", file=sys.stderr)
        processed += len(batch)

    print("=" * 72)
    print(
        f"done. processed={processed} updated={updated} "
        f"no_events_in_ch={no_events} failed={failed}"
    )
    print("re-run with the same args to retry any failed rows; "
          "persons that now have $created are skipped automatically.")


def flush_batch(batch: list, team_id: int, create_person_fn, transaction_module) -> None:
    """One batch: PG bulk_update, then CH person updates per row.
    Splitting the PG transaction from the CH produce loop is deliberate — the
    CH produce is best-effort and we don't want to roll back the PG update if
    one Kafka message fails (CH ReplacingMergeTree will eventually reconcile).
    """
    Person = batch[0].__class__
    with transaction_module.atomic():
        Person.objects.bulk_update(batch, fields=["properties", "version"], batch_size=len(batch))

    for person in batch:
        create_person_fn(
            team_id=team_id,
            uuid=str(person.uuid),
            properties=person.properties,
            is_identified=person.is_identified,
            version=person.version,
            created_at=None,  # leave CH person.created_at alone; only properties change
        )


if __name__ == "__main__":
    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "posthog.settings")
    django.setup()

    main()
