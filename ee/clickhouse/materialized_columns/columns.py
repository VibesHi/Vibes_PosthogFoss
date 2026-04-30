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


def drop_column(*args, **kwargs) -> None:
    return None


class MaterializedColumnDetails:
    """Upstream describes a materialized column; FOSS instances are never created."""

    def __init__(self, *args, **kwargs):
        pass


class MaterializedColumn:
    """Upstream represents a single materialized column on a CH table. FOSS inert."""

    def __init__(self, *args, **kwargs):
        pass


class _IndexBase:
    """Common stub for all materialized-column index variants.

    Upstream these are dataclass-like configs handed to ALTER TABLE ADD INDEX.
    On FOSS we never run ALTER, so any kwargs accepted is a no-op."""

    def __init__(self, *args, **kwargs):
        pass


class BloomFilterIndex(_IndexBase):
    pass


class MinMaxIndex(_IndexBase):
    pass


class NgramLowerIndex(_IndexBase):
    pass


def check_index_exists(*args, **kwargs) -> bool:
    """Upstream queries system.data_skipping_indices. FOSS: pretend nothing exists."""
    return False


# Upstream `tables` is a tuple of CH table names that support materialization.
# Empty here so callers iterate over nothing and produce no ALTER statements.
tables: tuple[str, ...] = ()
