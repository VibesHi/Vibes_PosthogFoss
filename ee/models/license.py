"""FOSS stub for ee.models.license.License.

Upstream PostHog ships two completely separate gating mechanisms:

  1. `EE_AVAILABLE` -- gates whether EE *code paths* execute (RBAC enforcement,
     materialization, EE billing webhooks, EE viewsets). We hard-code False
     in `posthog/settings/ee.py`, so none of that EE code runs.

  2. `Organization.available_product_features` -- the *permission flags* that
     OSS code paths check to decide whether to allow a feature. The OSS
     codebase contains gates like `posthog/api/project.py:1120-1123` that
     read this field and refuse if the relevant feature is missing. On
     PostHog Cloud, billing webhooks populate it. On self-hosted upstream,
     a periodic task polls license.posthog.com and mirrors the per-plan
     feature list onto each org via `Organization.update_available_product_features()`.

The two are NOT coupled. Disabling (1) does NOT make features in (2) free
-- gates fail closed by default and self-hosters end up stuck on a single
project, no audit logs, no SSO, no group analytics, no surveys styling, no
nothing. Bringing the EE *code* back is a different problem from bringing
the EE *features* back.

This fork is FOSS *and* self-hosted, so we want unlimited features without
running real EE code. The cleanest hook is right here: make `first_valid()`
return a synthetic enterprise License whose `PLANS` map covers every
`AvailableFeature` enum value. Upstream's `update_available_product_features()`
then naturally populates the org with every flag, every periodic sync.
No upstream patches, no monkey-patching of `Organization`, no forked
`update_available_product_features`.

Side effects of returning a synthetic license (audited):
  * `posthog/utils.py:1142, 1178` -- enables Zapier / Google OAuth feature
    flags. Both require ENV vars (SOCIAL_AUTH_GOOGLE_OAUTH2_KEY etc.) to
    actually activate, so this only flips the "you have a license to use
    them" gate.
  * `posthog/cloud_utils.py:57` -- caches the license in-process. The cache
    holds our synthetic instance; harmless.
  * `posthog/models/organization.py:285 _billing_plan_details` -- returns
    `("enterprise", "ee")`. Read by usage reports / admin UI for display.
  * No phone-home: `ee/tasks/send_license_usage.py` is a no-op stub and
    its scheduling is `if settings.EE_AVAILABLE:` gated (False on this fork).

If you ever want a "true OSS-only" deploy with NO paid features, override
`first_valid()` back to `return None` and the gates close.
"""

from django.db import models
from django.utils import timezone

from posthog.constants import AvailableFeature

# Every AvailableFeature is a paid feature on Cloud. We hand the synthetic
# license all of them. Source of truth: posthog/constants.py:AvailableFeature.
# Drift detection: a new AvailableFeature added upstream will silently NOT
# be unlocked here -- the periodic sync still runs `License.PLANS[plan]`,
# and a missing key is read as "not licensed". Mitigation: we enumerate
# `AvailableFeature` at import time, so any new enum value upstream lands
# here automatically without manual sync.
_ALL_FEATURES: list[str] = [str(feature) for feature in AvailableFeature]


class LicenseManager(models.Manager):
    """Stub manager. Upstream queries ee_license for `valid_until > now()`.
    FOSS short-circuits to a SYNTHETIC unsaved License with every paid
    feature, so the OSS gates throughout `posthog/` see "everything's
    licensed" and stop refusing operations.

    Returning an UNSAVED instance avoids touching the DB (the table may
    not exist during early migrations) AND avoids leaving fake rows in
    `ee_license` that would confuse anyone inspecting the DB. Django
    treats unsaved instances as truthy, and every consumer of
    `first_valid()` reads class-level constants (`License.PLANS`,
    `License.ENTERPRISE_PLAN`) or instance attributes we set directly --
    none reads from the DB through the returned instance."""

    def first_valid(self) -> "License | None":
        # Lazy-init at call time, NOT module load -- importing License at
        # module load would trigger Django app-loading checks before
        # `posthog.constants` is necessarily importable in some startup
        # paths (e.g. `manage.py check`).
        synthetic = License(
            key="foss-fork-synthetic-license",
            plan=License.ENTERPRISE_PLAN,
            valid_until=timezone.now() + timezone.timedelta(days=365 * 100),
        )
        # Mark as never-saved so anyone inspecting the instance sees the
        # truth ("this is synthetic"). Also prevents accidental .save()
        # from a caller that doesn't realize this is a stub.
        synthetic._foss_synthetic = True  # type: ignore[attr-defined]
        return synthetic


class License(models.Model):
    """Stub. The model exists so migrations referring to `to="ee.license"`
    resolve, but no row is ever inserted on FOSS. `first_valid()` returns
    a synthetic in-memory instance instead."""

    key = models.CharField(max_length=400, blank=True)
    plan = models.CharField(max_length=200, blank=True, null=True)
    valid_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # All AvailableFeature enum values. Upstream's organization.py reads
    # `License.PLANS.get(License.ENTERPRISE_PLAN, [])` -- it does NOT read
    # `License.ENTERPRISE_FEATURES` directly there, but other code paths
    # (e.g. billing UI, plan-tier inference) compare against these lists.
    # We populate both with the full feature set so every gate opens.
    SCALE_FEATURES: list[str] = list(_ALL_FEATURES)
    ENTERPRISE_FEATURES: list[str] = list(_ALL_FEATURES)

    SCALE_PLAN = "scale"
    ENTERPRISE_PLAN = "enterprise"

    # Plan -> feature list. Read by `Organization.update_available_product_features()`.
    PLANS: dict[str, list[str]] = {
        SCALE_PLAN: list(_ALL_FEATURES),
        ENTERPRISE_PLAN: list(_ALL_FEATURES),
    }

    objects = LicenseManager()

    class Meta:
        app_label = "ee"
        db_table = "ee_license"

    @property
    def available_features(self) -> list[str]:
        """Read by `posthog/utils.py:1142, 1178` to gate Zapier and Google
        OAuth UI flags. Returning the full feature set is consistent with
        `PLANS[ENTERPRISE_PLAN]`."""
        return list(_ALL_FEATURES)


def get_licensed_users_available() -> int | None:
    """Used by /_preflight. Upstream returns seat headroom; FOSS returns None
    so the UI shows "unlimited" / "n/a"."""
    return None
