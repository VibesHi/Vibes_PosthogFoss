"""FOSS stub for ee.hogai.session_summaries.session_group.patterns.

Imported by Temporal session-summary activities/workflows that never run on a
FOSS deploy. All names are inert.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EnrichedSessionGroupSummaryPatternsList:
    patterns: list[Any] = field(default_factory=list)


@dataclass
class SessionGroupSummaryPatternsList:
    patterns: list[Any] = field(default_factory=list)


@dataclass
class RawSessionGroupSummaryPattern:
    pattern_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class RawSessionGroupSummaryPatternsList:
    patterns: list[RawSessionGroupSummaryPattern] = field(default_factory=list)


@dataclass
class RawSessionGroupPatternAssignmentsList:
    assignments: list[Any] = field(default_factory=list)


def combine_patterns_assignments_from_single_session_summaries(*args, **kwargs) -> Any:
    return None


def combine_patterns_ids_with_events_context(*args, **kwargs) -> Any:
    return None


def combine_patterns_with_events_context(*args, **kwargs) -> Any:
    return None


def create_event_ids_mapping_from_ready_summaries(*args, **kwargs) -> dict[str, Any]:
    return {}


def get_persons_for_sessions_from_distinct_ids(*args, **kwargs) -> dict[str, Any]:
    return {}
