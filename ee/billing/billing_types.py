"""FOSS stub for ee.billing.billing_types."""

from enum import Enum


class BillingProvider(str, Enum):
    """Mirrors upstream provider enum. FOSS: defined but never compared against."""

    STRIPE = "stripe"
    VERCEL = "vercel"
    NONE = "none"


from dataclasses import dataclass, field
from typing import Any


@dataclass
class BillingStatus:
    """Snapshot of an org's billing state. FOSS: usage_report.py reads `.has_active_subscription` etc. — defaults are safe."""

    has_active_subscription: bool = False
    is_deactivated: bool = False
    plan: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
