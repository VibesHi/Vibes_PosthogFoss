"""FOSS stub for ee.hogai.utils.types."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AssistantState:
    """Container the upstream agent loop carries between graph nodes. FOSS: unused."""

    messages: list[Any] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


# NodePath is a list of strings representing a graph traversal path; can be
# imported as a type alias (`from ee.hogai.utils.types import NodePath`).
NodePath = list[str]
