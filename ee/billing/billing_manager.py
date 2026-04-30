"""FOSS stub for ee.billing.billing_manager.

Upstream: BillingManager talks to PostHog Cloud's billing API to enforce
quotas, fetch plan info, and gate paid features. FOSS: no billing service,
all "is_quota_exceeded" / "is_feature_available" checks return defaults
that allow the operation through.
"""

from typing import Any


class BillingManager:
    """No-op stub. Never makes outbound HTTP calls."""

    def __init__(self, *args, **kwargs):
        pass

    def get_billing(self, *args, **kwargs) -> dict[str, Any]:
        return {}

    def update_billing(self, *args, **kwargs) -> None:
        return None

    def deactivate_products(self, *args, **kwargs) -> None:
        return None

    def update_billing_admin_emails(self, *args, **kwargs) -> None:
        return None

    def update_billing_organization_users(self, *args, **kwargs) -> None:
        return None


def build_billing_token(*args, **kwargs) -> str:
    """Upstream: signed JWT for the billing service. FOSS: empty string."""
    return ""
