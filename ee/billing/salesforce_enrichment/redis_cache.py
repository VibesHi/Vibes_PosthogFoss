"""FOSS stub for ee.billing.salesforce_enrichment.redis_cache."""

from typing import Any


def get_cached_account(*args, **kwargs) -> Any:
    return None


def set_cached_account(*args, **kwargs) -> None:
    return None


def delete_cached_account(*args, **kwargs) -> None:
    return None


def get_cached_signals(*args, **kwargs) -> Any:
    return None


def set_cached_signals(*args, **kwargs) -> None:
    return None


def get_cached_accounts_count(*args, **kwargs) -> int:
    return 0


def store_accounts_in_redis(*args, **kwargs) -> None:
    return None


def get_cached_org_mappings_count(*args, **kwargs) -> int:
    """Upstream returns the count of cached PostHog→Salesforce org mappings."""
    return 0


def store_org_mappings_in_redis(*args, **kwargs) -> None:
    return None


def get_org_mappings_page_from_redis(*args, **kwargs) -> list[Any]:
    return []


def get_stripe_enrichment_watermark(*args, **kwargs) -> Any:
    """Upstream returns the last-processed Stripe event timestamp."""
    return None


def set_stripe_enrichment_watermark(*args, **kwargs) -> None:
    return None
