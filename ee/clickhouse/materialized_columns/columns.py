"""FOSS stub for ee.clickhouse.materialized_columns.columns.

Upstream: provides `materialize(table, property, column_name)` to ALTER a
ClickHouse table and add a materialized column for a JSON property -- a
significant query-performance optimization. FOSS: no auto-materialization;
queries that filter on JSON properties stay slower (read JSON at query time).

This stub keeps the module importable; CH migrations 0019 and 0026 wrap the
import in try/except, so even if absent they're no-ops -- present-with-noop
behaviour is the same. Same for posthog/clickhouse/materialized_columns.py
which gates this behind `if EE_AVAILABLE:` (False on FOSS).
"""

from typing import Any


def materialize(*args, **kwargs) -> None:
    """No-op. Real upstream emits an ALTER TABLE ADD COLUMN MATERIALIZED."""
    return None


def get_enabled_materialized_columns(*args, **kwargs) -> dict:
    """Returns empty dict so callers iterate over no materialized columns."""
    return {}


def get_materialized_columns(*args, **kwargs) -> dict:
    return {}


def backfill_materialized_columns(*args, **kwargs) -> Any:
    return None


def drop_materialized_column(*args, **kwargs) -> None:
    return None


def update_column_is_disabled(*args, **kwargs) -> None:
    return None
