"""FOSS stub for ee.hogai.session_summaries.session_group.patterns."""

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
