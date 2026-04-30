"""FOSS stub for ee.billing.salesforce_enrichment.enrichment."""

from typing import Any


def bulk_update_salesforce_accounts(*args, **kwargs) -> Any:
    return None


async def enrich_accounts_async(*args, **kwargs) -> Any:
    """Upstream Temporal activity that hydrates Salesforce account fields from
    PostHog data. FOSS: no Salesforce, no-op."""
    return None
