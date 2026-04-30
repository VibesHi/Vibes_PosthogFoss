"""FOSS stub for ee.billing.salesforce_enrichment.stripe_signals."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StripeSignals:
    customer_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def fetch_stripe_signals(*args, **kwargs) -> StripeSignals:
    return StripeSignals()
