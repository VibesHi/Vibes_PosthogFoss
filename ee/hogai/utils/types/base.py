"""FOSS stub for ee.hogai.utils.types.base."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AssistantGraphName(str, Enum):
    """Identifiers for the different agent loop graphs upstream registers."""

    DEFAULT = "default"
    RESEARCH = "research"
    CHAT = "chat"


class AssistantNodeName(str, Enum):
    """Identifiers for graph nodes used in products/*/backend/max_tools.py.
    Upstream covers ~30 nodes; FOSS only needs the names to be importable."""

    START = "start"
    END = "end"
    TOOLS = "tools"
    AGENT = "agent"
    HUMAN = "human"


@dataclass
class AssistantState:
    """Re-exported here because some imports use base directly."""

    messages: list[Any] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ContextMessage:
    """Message wrapper used by the chat-agent runner."""

    role: str = ""
    content: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


# Re-export NodePath at this path too.
NodePath = list[str]
