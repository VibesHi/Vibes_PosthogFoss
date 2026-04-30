"""FOSS stub. Used in products/experiments/backend/max_tools.py (Max-AI tool, not loaded)."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExperimentContext:
    experiment_id: int = 0
    extra: dict[str, Any] = field(default_factory=dict)
