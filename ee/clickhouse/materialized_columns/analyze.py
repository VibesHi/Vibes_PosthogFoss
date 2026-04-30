"""FOSS stub. Used by:
  - posthog/dags/create_materialized_column.py (Dagster job, never loaded on self-host)
  - posthog/management/commands/generate_demo_data.py (only on `manage.py generate_demo_data`)
  - posthog/tasks/tasks.py (Celery scheduled task; only fires if scheduled)

All call sites are gated/lazy. No-op task is safe.
"""


def materialize_properties_task(*args, **kwargs) -> None:
    """Real upstream backfills materialized columns for hot properties. FOSS: noop."""
    return None


# Some callers do `materialize_properties_task.delay(...)` (Celery shared_task).
materialize_properties_task.delay = lambda *a, **kw: None  # type: ignore[attr-defined]
