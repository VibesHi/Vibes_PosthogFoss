"""FOSS stub: Vercel marketplace background tasks.

posthog/migrations/1074_backfill_vercel_connectable_resources.py is a
RunPython data migration that imports `backfill_vercel_connectable_resources`
and calls `.delay()` (Celery shared_task pattern). On a FOSS deploy this
should be a no-op so the migration applies cleanly.
"""


def _noop_delay(*args, **kwargs) -> None:
    return None


def backfill_vercel_connectable_resources(*args, **kwargs) -> None:
    """No-op. Real upstream task syncs Vercel-managed resources back to PostHog."""
    return None


backfill_vercel_connectable_resources.delay = _noop_delay  # type: ignore[attr-defined]
