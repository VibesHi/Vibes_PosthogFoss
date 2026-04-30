"""FOSS stub. Used in products/conversations/backend/ai/mode_manager.py."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentModeDefinition:
    name: str = ""
    description: str = ""
    config: dict[str, Any] = field(default_factory=dict)
