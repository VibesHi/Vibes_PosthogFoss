"""FOSS stub for ee.hogai.session_summaries.session.summarize_session.

Imported by posthog/temporal/session_replay/session_summary/* (Temporal
workflows that never run on FOSS) and posthog/session_recordings/session_recording_api.py
(unused method paths).
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExtraSummaryContext:
    user_role: str | None = None
    custom_instructions: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class SingleSessionSummaryLlmInputs:
    session_id: str = ""
    events: list[Any] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


def summarize_session(*args, **kwargs) -> Any:
    return None


def execute_summarize_session(*args, **kwargs) -> Any:
    return None


async def execute_summarize_session_video_stream(*args, **kwargs):
    """Async generator stub. Never iterated on FOSS."""
    if False:
        yield  # pragma: no cover


@dataclass
class SessionSummaryDBData:
    session_id: str = ""
    events: list[Any] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def get_session_data_from_db(*args, **kwargs) -> SessionSummaryDBData:
    """Upstream pulls events + metadata from posthog DB + ClickHouse. FOSS: empty."""
    return SessionSummaryDBData()


def prepare_data_for_single_session_summary(*args, **kwargs) -> Any:
    """Upstream filters/groups raw events into the LLM-ready shape. FOSS: empty."""
    return SessionSummaryDBData()


def prepare_single_session_summary_input(*args, **kwargs) -> SingleSessionSummaryLlmInputs:
    return SingleSessionSummaryLlmInputs()
