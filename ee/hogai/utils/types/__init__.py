"""FOSS stub for ee.hogai.utils.types."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


@dataclass
class AssistantState:
    """Container the upstream agent loop carries between graph nodes. FOSS: unused."""

    messages: list[Any] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class PartialAssistantState:
    """Like AssistantState but every field optional; used for graph node updates."""

    messages: list[Any] | None = None
    extra: dict[str, Any] | None = None


@dataclass
class AssistantOutput:
    """Final output of an assistant run."""

    content: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class AssistantMode(str, Enum):
    """Upstream switches the agent loop on this. FOSS: defined, never compared against."""

    DEFAULT = "default"
    RESEARCH = "research"
    CHAT = "chat"


# NodePath is a list of strings representing a graph traversal path; can be
# imported as a type alias (`from ee.hogai.utils.types import NodePath`).
NodePath = list[str]
