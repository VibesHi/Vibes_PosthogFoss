"""FOSS stub for ee.models.license.License.

Upstream: validates a paid PostHog license key and gates EE features.
FOSS: no license rows ever exist, so OSS license checks always see
"unlicensed" and gate EE features off (which is what we want).
"""

from django.db import models


class LicenseManager(models.Manager):
    """Stub manager. Upstream's manager runs a SQL query against ee_license
    to find the first row with `valid_until > now()`. FOSS short-circuits to
    None without touching the DB so /preflight, /home, and any other code
    path that calls License.objects.first_valid() returns "no license"
    consistently — even before migrations have created the table."""

    def first_valid(self) -> "License | None":
        return None


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

    # Plan name constants. Used by `posthog/models/organization.py` as
    # `License.ENTERPRISE_PLAN` after a license is found. FOSS never finds
    # one, so these are only defined to keep `getattr(License, "...", ...)`
    # paths happy.
    SCALE_PLAN = "scale"
    ENTERPRISE_PLAN = "enterprise"

    # Plan -> feature list lookup. Read by organization.py after a successful
    # first_valid() — FOSS path never reaches it (license is always None).
    PLANS: dict[str, list[str]] = {}

    objects = LicenseManager()

    class Meta:
        app_label = "ee"
        db_table = "ee_license"


def get_licensed_users_available() -> int | None:
    """Used by /_preflight. Upstream returns seat headroom; FOSS returns None
    so the UI shows "unlimited" / "n/a"."""
    return None
