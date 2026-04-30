"""FOSS stub for ee.billing.salesforce_enrichment.usage_signals."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class UsageSignals:
    org_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
