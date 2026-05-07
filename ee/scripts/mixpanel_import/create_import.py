"""Create a BatchImport row that points the Rust batch-import-worker at our
GCS bucket of daily Mixpanel files and drains directly into the historical
Kafka topic.

Why this exists instead of using the managed-migration UI:
- The UI's `BatchImportS3SourceCreateSerializer` doesn't expose `endpoint_url`,
  so it can't talk to GCS via S3 interop. We bypass it by writing
  `BatchImport.import_config` JSON by hand.
- The UI hardcodes `to_capture(send_rate=1000)`. Capture HTTP at 1k events/sec
  is the slow path; for ~400 M events on this fork we want `to_kafka` direct
  to `events_plugin_ingestion_historical` (~50× faster, see RUNBOOK).
- This script lives under ee/ so we don't patch any upstream code.

Run it from the prod host:

    docker compose -f docker-compose.prod.yml exec -T web \\
        python -m ee.scripts.mixpanel_import.create_import

All credentials come from environment variables (NOT CLI args, which leak
into shell history and `ps` output). Set them on the host BEFORE the
`docker compose exec` so they're forwarded to the container, OR use
`docker compose --env-file …` / `-e KEY=$KEY`.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

# NOTE: We deliberately don't use the `logging` module for operator output here —
# Django's structlog config takes over the root logger during `django.setup()`,
# so anything written via `logger.info(...)` from this script gets swallowed.
# All operator-facing output goes through print() instead.


# GCS env-var convention:
# We share the analytics bucket with `posthog-events-export` (different subdir
# per use case). Same SA, same HMAC pair → reuse `EVENTS_EXPORT_GCS_*` directly
# instead of duplicating creds in a Mixpanel-specific namespace.
#
# Do NOT use `GCS_BUCKET` / `GCS_HMAC_*` — those belong to the backup container
# and point at a different bucket. Collision footgun.
REQUIRED_ENV = (
    "POSTHOG_TEAM_ID",
    "EVENTS_EXPORT_GCS_BUCKET",
    "EVENTS_EXPORT_GCS_HMAC_KEY",
    "EVENTS_EXPORT_GCS_HMAC_SECRET",
)

DEFAULTS = {
    # Only the prefix is Mixpanel-specific. Bucket and creds come from EVENTS_EXPORT_*.
    # Per-month invocations narrow with `--prefix mixpanel-events/moonx/2024/01/`.
    "MIXPANEL_IMPORT_GCS_PREFIX": "mixpanel-events/moonx/",
    "MIXPANEL_IMPORT_GCS_REGION": "auto",
    "MIXPANEL_IMPORT_GCS_ENDPOINT_URL": "https://storage.googleapis.com",
    # The Rust worker treats `sink.topic` as a logical alias, not the actual
    # Kafka topic name. Valid values: "main", "historical", "overflow". They
    # resolve via KAFKA_TOPIC_MAIN / KAFKA_TOPIC_HISTORICAL / KAFKA_TOPIC_OVERFLOW
    # env vars on the worker (set in docker-compose.prod.yml).
    "KAFKA_TOPIC_ALIAS": "historical",
    # 1500 events/sec — paced just below the EMPIRICALLY MEASURED ceiling of 4
    # default-config ingestion-general replicas (~1700 events/sec aggregate, or
    # ~425/sec per replica with CONSUMER_MAX_BACKGROUND_TASKS=1 + CONSUMER_BATCH_SIZE=500).
    # Setting send_rate slightly below consumer ceiling keeps Kafka lag flat or
    # decreasing, eliminating retention-loss risk entirely.
    #
    # Why this matters: on a 6h-retention cluster, lag that grows faster than
    # consumers drain GETS DELETED before consumption. The first March attempt
    # at 80,000/sec lost 144k events on partition 0 (10x the consumer ceiling
    # → lag exceeded 6h × 1700/s = ~36M event buffer → retention purged it).
    # See RUNBOOK "lessons learned" section.
    #
    # If you bump retention to 7d AND tune consumers (CONSUMER_MAX_BACKGROUND_TASKS=4,
    # CONSUMER_BATCH_SIZE=2000, replicas=6, partitions>=6), you can safely raise
    # this to ~15-20k. Otherwise leave it at 1500 — slow-and-correct beats
    # fast-and-lossy. Confirm consumer drain rate empirically with rps.sh
    # BEFORE raising this past 1500.
    #
    # Override per-job with KAFKA_SEND_RATE env or --kafka-send-rate.
    "KAFKA_SEND_RATE": "1500",
    "KAFKA_TXN_TIMEOUT_S": "60",
    "MIXPANEL_TIMESTAMP_OFFSET_SECONDS": "0",
    "MIXPANEL_SKIP_NO_DISTINCT_ID": "false",
}


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        # print() not logger — see DRY RUN comment in main() for why.
        print(f"ERROR: missing required env var: {name}", file=sys.stderr)
        sys.exit(2)
    return value


def _bucket_name() -> str:
    """`EVENTS_EXPORT_GCS_BUCKET` is stored with a `gs://` prefix in .env.
    The Rust worker's S3-interop client wants the bare name."""
    return os.environ["EVENTS_EXPORT_GCS_BUCKET"].removeprefix("gs://").rstrip("/")


def build_import_config(args: argparse.Namespace) -> dict[str, Any]:
    """Hand-crafted JSON that mirrors what BatchImportConfigBuilder would
    produce, plus the GCS S3-interop `endpoint_url` override that the Python
    builder doesn't expose. The Rust worker reads this verbatim."""
    return {
        "data_format": {
            "type": "json_lines",
            "skip_blanks": True,
            "content": {
                "type": "mixpanel",
                "skip_no_distinct_id": args.skip_no_distinct_id,
                "timestamp_offset_seconds": args.timestamp_offset_seconds,
            },
        },
        "source": {
            "type": "s3_gzip",
            "bucket": args.bucket,
            "prefix": args.prefix,
            "region": args.region,
            "endpoint_url": args.endpoint_url,
            "access_key_id_key": "aws_access_key_id",
            "secret_access_key_key": "aws_secret_access_key",
        },
        "sink": {
            "type": "kafka",
            "topic": args.kafka_topic,
            "send_rate": args.kafka_send_rate,
            "transaction_timeout_seconds": args.kafka_txn_timeout_s,
        },
        # `import_events: true` is the default in BatchImportConfigBuilder but
        # we set it explicitly so the worker contract is unambiguous.
        "import_events": True,
    }


def build_secrets(access_key_id: str, secret_access_key: str) -> dict[str, str]:
    """The worker decrypts BatchImport.secrets using ENCRYPTION_KEYS env var
    and looks up the values by the keys we configured in `source` above."""
    return {
        "aws_access_key_id": access_key_id,
        "aws_secret_access_key": secret_access_key,
    }


def main() -> None:
    # Validate required env up front. Bail early so we don't half-create rows.
    for var in REQUIRED_ENV:
        if not os.environ.get(var):
            print(f"ERROR: missing required env var: {var}", file=sys.stderr)
            sys.exit(2)

    parser = argparse.ArgumentParser(
        description="Create a BatchImport row pointing at a GCS prefix of "
        "Mixpanel daily JSONL.gz files, draining to the historical Kafka topic.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--prefix",
        default=_env("MIXPANEL_IMPORT_GCS_PREFIX", DEFAULTS["MIXPANEL_IMPORT_GCS_PREFIX"]),
        help="GCS object prefix (e.g. 'mixpanel-events/moonx/2024/01/')",
    )
    parser.add_argument(
        "--bucket",
        default=_bucket_name(),
        help="GCS bucket name (default: stripped from $EVENTS_EXPORT_GCS_BUCKET)",
    )
    parser.add_argument(
        "--region",
        default=_env("MIXPANEL_IMPORT_GCS_REGION", DEFAULTS["MIXPANEL_IMPORT_GCS_REGION"]),
        help="AWS-SDK region label (any string, GCS ignores it; 'auto' is fine)",
    )
    parser.add_argument(
        "--endpoint-url",
        default=_env("MIXPANEL_IMPORT_GCS_ENDPOINT_URL", DEFAULTS["MIXPANEL_IMPORT_GCS_ENDPOINT_URL"]),
        help="GCS S3 interop endpoint",
    )
    parser.add_argument(
        "--team-id",
        type=int,
        default=int(_env("POSTHOG_TEAM_ID")),
        help="Target PostHog team_id (events ingest under this team's token)",
    )
    parser.add_argument(
        "--kafka-topic",
        default=_env("KAFKA_TOPIC_ALIAS", DEFAULTS["KAFKA_TOPIC_ALIAS"]),
        choices=("main", "historical", "overflow"),
        help="Logical Kafka topic alias resolved by the worker via KAFKA_TOPIC_* env",
    )
    parser.add_argument(
        "--kafka-send-rate",
        type=int,
        default=int(_env("KAFKA_SEND_RATE", DEFAULTS["KAFKA_SEND_RATE"])),
        help="Target events/sec sent to Kafka per worker; tune per Kafka headroom",
    )
    parser.add_argument(
        "--kafka-txn-timeout-s",
        type=int,
        default=int(_env("KAFKA_TXN_TIMEOUT_S", DEFAULTS["KAFKA_TXN_TIMEOUT_S"])),
        help="Kafka producer transaction timeout (seconds)",
    )
    parser.add_argument(
        "--timestamp-offset-seconds",
        type=int,
        default=int(_env("MIXPANEL_TIMESTAMP_OFFSET_SECONDS", DEFAULTS["MIXPANEL_TIMESTAMP_OFFSET_SECONDS"])),
        help="Adjust every event timestamp by this many seconds (rare; usually 0)",
    )
    parser.add_argument(
        "--skip-no-distinct-id",
        action="store_true",
        default=_env("MIXPANEL_SKIP_NO_DISTINCT_ID", DEFAULTS["MIXPANEL_SKIP_NO_DISTINCT_ID"]).lower() == "true",
        help="Drop events that have no distinct_id (default: synthesize a UUIDv7)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the import_config JSON and the masked secrets but do NOT save the row",
    )
    args = parser.parse_args()

    # Imports are deferred until after env validation so a missing env var
    # produces a clean error before Django spins up its app registry.
    from posthog.models.batch_imports import BatchImport
    from posthog.models.team.team import Team

    try:
        team = Team.objects.get(pk=args.team_id)
    except Team.DoesNotExist:
        print(f"ERROR: team_id={args.team_id} does not exist", file=sys.stderr)
        sys.exit(3)

    import_config = build_import_config(args)
    secrets = build_secrets(
        access_key_id=os.environ["EVENTS_EXPORT_GCS_HMAC_KEY"],
        secret_access_key=os.environ["EVENTS_EXPORT_GCS_HMAC_SECRET"],
    )

    if args.dry_run:
        import json

        masked = {**secrets, "aws_secret_access_key": "***REDACTED***"}
        # NOTE: print() not logger.info — Django's structlog already configured the
        # root logger by the time we get here, so logging.basicConfig() above is a
        # no-op and our INFO logs would never reach stdout. Operator output goes
        # via print() to dodge that bootstrap order.
        print("DRY RUN - would create BatchImport with:")
        print(f"  team_id={team.id} name={team.name}")
        print(f"  import_config={json.dumps(import_config, indent=2)}")
        print(f"  secrets={json.dumps(masked, indent=2)}")
        return

    # NOTE: We bypass the BatchImportConfigBuilder to write the JSON directly
    # because the builder doesn't expose `endpoint_url` (needed for GCS S3
    # interop). The schema is otherwise identical — the Rust worker validates
    # it via serde on lease.
    bi = BatchImport(
        team=team,
        created_by_id=None,  # script-driven; no owning user
        import_config=import_config,
        secrets=secrets,
        status=BatchImport.Status.RUNNING,
    )
    bi.save()

    print(f"created BatchImport id={bi.id} team_id={bi.team_id} status={bi.status}")
    print("monitor with:")
    print(
        "  docker compose exec db psql -U posthog -d posthog -c "
        '"SELECT id, status, leased_until, backoff_attempt, '
        "left(coalesce(status_message,''), 200) as status_message, "
        "coalesce(state::text, '') as state "
        f"FROM posthog_batchimport WHERE id = '{bi.id}';\""
    )


if __name__ == "__main__":
    # When invoked as `python -m ee.scripts.mixpanel_import.create_import`,
    # Django's `manage.py shell` style auto-setup doesn't run. Bootstrap manually.
    if "DJANGO_SETTINGS_MODULE" not in os.environ:
        os.environ["DJANGO_SETTINGS_MODULE"] = "posthog.settings"
    import django

    django.setup()
    main()
