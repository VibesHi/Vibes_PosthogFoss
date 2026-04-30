"""FOSS stub. Used in products/conversations/backend/ai/mode_manager.py
and other product agent-mode definitions.

The real EE implementation is a structured dataclass with mode/toolkit/etc.
Here we accept arbitrary keyword arguments and stash them so module-top
constructions like:

    SUPPORT_MODE = AgentModeDefinition(
        mode=AgentMode.PRODUCT_ANALYTICS,
        mode_description="Support agent mode",
        toolkit_class=AgentToolkit,
    )

succeed at import time without us having to track every kwarg upstream
adds. Instances are inert; the gated EE feature paths that consume them
are disabled via EE_AVAILABLE=False.
"""

from typing import Any


class AgentModeDefinition:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._args = args
        self._kwargs = kwargs
        for key, value in kwargs.items():
            setattr(self, key, value)
