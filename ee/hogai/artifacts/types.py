"""FOSS stub. Used in products/alerts/backend/max_tools.py (Max-AI tool, never loaded)."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ModelArtifactResult:
    artifact_type: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
