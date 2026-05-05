from typing import Protocol

from posthog.models.instance_setting import get_instance_setting
from posthog.models.property import PropertyName, TableColumn, TableWithProperties
from posthog.settings import EE_AVAILABLE

ColumnName = str
TablesWithMaterializedColumns = TableWithProperties
MATERIALIZATION_VALID_TABLES = {"events", "person", "groups"}


class MaterializedColumn(Protocol):
    name: ColumnName
    is_nullable: bool
    has_minmax_index: bool
    has_bloom_filter_index: bool
    has_ngram_lower_index: bool


if EE_AVAILABLE:
    from ee.clickhouse.materialized_columns.columns import get_enabled_materialized_columns

    def get_materialized_column_for_property(
        table: TablesWithMaterializedColumns, table_column: TableColumn, property_name: PropertyName
    ) -> MaterializedColumn | None:
        if not get_instance_setting("MATERIALIZED_COLUMNS_ENABLED"):
            return None

        return get_enabled_materialized_columns(table).get((property_name, table_column))
else:
    # FOSS patch: behave the same as the `if EE_AVAILABLE:` branch above. The
    # `ee.clickhouse.materialized_columns.columns` module in this fork is a
    # faithful port of upstream (no EE-licensed code paths), so the read-path
    # introspection works without flipping the global EE_AVAILABLE gate (which
    # would unleash ~20 other EE branches across RBAC, Vercel, scheduled
    # subscriptions, etc. — see posthog/settings/ee.py for rationale).
    #
    # Two effects:
    #   1. Unconditional `from posthog.clickhouse.materialized_columns import
    #      get_enabled_materialized_columns` (used in events_prefilter.py and
    #      others) doesn't ImportError.
    #   2. The HogQL printer's call to get_materialized_column_for_property
    #      (printer/base.py:45) now returns real MaterializedColumn objects, so
    #      `properties.$xxx` accesses get rewritten to `mat_$xxx` columns when
    #      they exist instead of falling back to JSONExtractRaw on the raw JSON.
    #
    # Documented in README.prod.md "FOSS source patches".
    from ee.clickhouse.materialized_columns.columns import get_enabled_materialized_columns

    def get_materialized_column_for_property(
        table: TablesWithMaterializedColumns, table_column: TableColumn, property_name: PropertyName
    ) -> MaterializedColumn | None:
        if not get_instance_setting("MATERIALIZED_COLUMNS_ENABLED"):
            return None

        return get_enabled_materialized_columns(table).get((property_name, table_column))
