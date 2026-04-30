"""FOSS stub for ee.hogai.context.context.AssistantContextManager."""

from typing import Any


class AssistantContextManager:
    """No-op. Instantiating raises -- guards against accidental Max AI invocation."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError("Max AI context unavailable in FOSS fork")

    def get_context(self, *args, **kwargs) -> dict[str, Any]:
        return {}
