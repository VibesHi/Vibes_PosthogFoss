"""FOSS stub. Lazy imported in posthog/session_recordings/session_recording_api.py."""

from typing import Any


def get_openai_client(*args, **kwargs) -> Any:
    """Real upstream returns a configured OpenAI client. FOSS: None."""
    return None
