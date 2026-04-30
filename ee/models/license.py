"""FOSS stub for ee.models.license.License.

Upstream: validates a paid PostHog license key and gates EE features.
FOSS: no license rows ever exist, so OSS license checks always see
"unlicensed" and gate EE features off (which is what we want).
"""

from django.db import models


class License(models.Model):
    """Stub. No instance is ever created on a FOSS deploy."""

    key = models.CharField(max_length=400, blank=True)
    plan = models.CharField(max_length=200, blank=True, null=True)
    valid_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # Class-level constants OSS code reads. Empty so feature membership tests
    # like `if feature in License.SCALE_FEATURES` always return False.
    SCALE_FEATURES: list[str] = []
    ENTERPRISE_FEATURES: list[str] = []

    class Meta:
        app_label = "ee"
        db_table = "ee_license"

    @classmethod
    def first_valid(cls) -> "License | None":
        """Upstream returns the first non-expired License row. FOSS has none."""
        return None


def get_licensed_users_available() -> int | None:
    """Used by /_preflight. Upstream returns seat headroom; FOSS returns None
    so the UI shows "unlimited" / "n/a"."""
    return None
