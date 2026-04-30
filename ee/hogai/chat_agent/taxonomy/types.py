"""FOSS stub."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TaxonomyAgentState:
    messages: list[Any] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
