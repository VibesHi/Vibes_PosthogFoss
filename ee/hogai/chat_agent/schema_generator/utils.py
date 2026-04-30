"""FOSS stub. Used in products/data_warehouse/backend/hogql_fixer_ai.py."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SchemaGeneratorOutput:
    schema: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
