"""FOSS stub for ee.billing.salesforce_enrichment.usage_signals."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class UsageSignals:
    org_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


async def aggregate_usage_signals_for_orgs(*args, **kwargs) -> list[UsageSignals]:
    """Upstream Temporal activity that rolls up org usage for Salesforce sync. FOSS: empty."""
    return []
