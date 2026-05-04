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
    # FOSS: re-export the EE stub's no-op so consumers that lazy-import this
    # symbol (e.g. `posthog/hogql_queries/web_analytics/events_prefilter.py`)
    # don't ImportError at request time and 500 the WebAnalytics tiles.
    # The stub returns an empty dict, which makes callers fall back to
    # JSONExtractRaw on the events.properties column. Slightly slower than
    # materialized columns but functionally correct.
    from ee.clickhouse.materialized_columns.columns import get_enabled_materialized_columns

    def get_materialized_column_for_property(
        table: TablesWithMaterializedColumns, table_column: TableColumn, property_name: PropertyName
    ) -> MaterializedColumn | None:
        return None
