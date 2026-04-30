"""FOSS stub for ee.billing.billing_types."""

from enum import Enum


class BillingProvider(str, Enum):
    """Mirrors upstream provider enum. FOSS: defined but never compared against."""

    STRIPE = "stripe"
    VERCEL = "vercel"
    NONE = "none"
