"""FOSS stub for ee.hogai.session_summaries.tracking."""

import uuid


def capture_session_summary_timing(*args, **kwargs) -> None:
    return None


def capture_session_summary_started(*args, **kwargs) -> None:
    return None


def generate_tracking_id(*args, **kwargs) -> str:
    """Upstream returns a UUID4 hex used to correlate analytics events for one
    session-summary run. FOSS: same shape, no-op behavior."""
    return uuid.uuid4().hex
