"""FOSS stub for ee.clickhouse.queries.groups_join_query.GroupsJoinQuery.

Upstream: builds the SQL fragment that joins event rows with group analytics
data. FOSS: group analytics is partially available (basic group queries
work without this EE-specific joiner). The OSS GroupsJoinQuery in
posthog/queries/groups_join_query/__init__.py wraps the import in try/except.
"""

from typing import Any


class GroupsJoinQuery:
    """No-op. Subclassing/instantiating raises NotImplementedError to make
    accidental usage loud rather than producing silently-wrong SQL."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError("EE GroupsJoinQuery unavailable in FOSS fork")

    def get_join_query(self) -> tuple[str, dict[str, Any]]:
        return "", {}
