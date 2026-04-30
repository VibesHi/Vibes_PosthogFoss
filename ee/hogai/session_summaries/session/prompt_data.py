"""FOSS stub for ee.hogai.session_summaries.session.prompt_data."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SessionSummaryPromptData:
    session_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
