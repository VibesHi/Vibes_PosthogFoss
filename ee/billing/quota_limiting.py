"""FOSS stub for ee.billing.quota_limiting.

Upstream: enforces team-level event/recording quotas via Redis-backed
counters synced from the billing service. FOSS: no quotas; all queries
return "no team is limited".

Hot path: posthog/api/feature_flag.py calls list_limited_team_attributes()
on every /decide request unless DECIDE_FEATURE_FLAG_QUOTA_CHECK is False.
The stub returns an empty set fast (no Redis/DB hit) so this is safe even
without disabling the setting.
"""

from enum import Enum
from typing import Any


class QuotaResource(str, Enum):
    EVENTS = "events"
    RECORDINGS = "recordings"
    ROWS_SYNCED = "rows_synced"
    FEATURE_FLAG_REQUESTS = "feature_flag_requests"
    API_QUERIES_READ_BYTES = "api_queries_read_bytes"
    EXCEPTIONS = "exceptions"
    LLM_EVENTS = "llm_events"


class QuotaLimitingCaches(str, Enum):
    QUOTA_LIMITER_CACHE_KEY = "@posthog/quota-limiting/"
    QUOTA_OVERAGE_RETENTION_CACHE_KEY = "@posthog/quota-limiting-overage-retention/"


def list_limited_team_attributes(resource: Any, *args, **kwargs) -> list[str]:
    """Returns team tokens currently over-quota for a resource. FOSS: never any."""
    return []


def add_limited_team_tokens(*args, **kwargs) -> None:
    return None


def remove_limited_team_tokens(*args, **kwargs) -> None:
    return None


def get_client(*args, **kwargs) -> Any:
    """Upstream returns a Redis client. FOSS callers only hand the result back
    to other stubs that don't use it, so None is safe."""
    return None


def org_quota_limited_until(*args, **kwargs) -> Any:
    return None


def update_org_billing_quotas(*args, **kwargs) -> None:
    return None


def update_all_orgs_billing_quotas(*args, **kwargs) -> None:
    """Upstream Celery/Temporal task that syncs billing service → Redis. FOSS: no billing."""
    return None


update_all_orgs_billing_quotas.delay = lambda *a, **kw: None  # type: ignore[attr-defined]


def is_team_limited(*args, **kwargs) -> bool:
    """Returns True if the team is currently over a quota. FOSS: never."""
    return False
