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
