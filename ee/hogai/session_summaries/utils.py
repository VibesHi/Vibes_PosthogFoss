"""FOSS stub for ee.hogai.session_summaries.utils."""

import datetime
from typing import Any, Iterable


def logging_session_ids(*args, **kwargs) -> str:
    return ""


def calculate_time_since_start(*args, **kwargs) -> float:
    return 0.0


def get_column_index(*args, **kwargs) -> int:
    return 0


def prepare_datetime(*args, **kwargs) -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def estimate_tokens_from_strings(strings: Iterable[str], *args, **kwargs) -> int:
    return 0


def serialize_to_sse_event(*args, **kwargs) -> str:
    return ""


def format_seconds_as_mm_ss(seconds: float) -> str:
    return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"


def parse_str_timestamp_to_s(*args, **kwargs) -> float:
    return 0.0


def unpack_full_event_id(*args, **kwargs) -> tuple[str, str]:
    """Upstream returns (session_id, event_id) from a packed identifier. FOSS:
    return empty strings; the calling activity won't run on a FOSS deploy."""
    return ("", "")


# Generic catch-all; some files import names not enumerated above.
def _stub(*args, **kwargs) -> Any:
    return None
