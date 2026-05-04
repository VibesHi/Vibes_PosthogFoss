"""FOSS-fork smoke probe.

Designed to run inside the `web` container with `DJANGO_SETTINGS_MODULE`
already set, either as the script body of `bin/foss-smoke-test` or piped
via stdin so it works on a freshly-pulled host whose container image
hasn't been rebuilt yet:

    docker compose exec -T web python3 - < bin/foss_smoke_test.py

Exits 0 on success, 1 if any check fails. Each failure prints what to
look at -- model FK / mixin method / route / migration backfill / etc.
"""

import importlib
import sys

import django

django.setup()

failures: list[str] = []

# -------------------------------------------------------------------
# 1) Confirm every ee.* module that OSS code imports loads cleanly.
# -------------------------------------------------------------------
EE_IMPORTS = [
    "ee.api.rbac.access_control",
    "ee.api.scim.utils",
    "ee.api.session_summaries",
    "ee.api.hooks",
    "ee.api.foss_stubs",
    "ee.api.ee_event_definition",
    "ee.api.ee_property_definition",
    "ee.api.vercel.tasks",
    "ee.api.vercel.vercel_installation",
    "ee.api.vercel.vercel_product",
    "ee.api.vercel.vercel_proxy",
    "ee.api.vercel.vercel_resource",
    "ee.models",
    "ee.models.license",
    "ee.models.conversation",
    "ee.models.dashboard_privilege",
    "ee.models.event_definition",
    "ee.models.property_definition",
    "ee.models.explicit_team_membership",
    "ee.models.feature_flag_role_access",
    "ee.models.scim_provisioned_user",
    "ee.models.scim_request_log",
    "ee.models.session_summaries",
    "ee.models.team_session_summaries_config",
    "ee.models.rbac.access_control",
    "ee.models.rbac.role",
    "ee.models.rbac.organization_resource_access",
    "ee.middleware",
    "ee.settings",
    "ee.urls",
]
for mod in EE_IMPORTS:
    try:
        importlib.import_module(mod)
    except Exception as e:
        failures.append(f"IMPORT FAIL {mod}: {type(e).__name__}: {e}")

# -------------------------------------------------------------------
# 2) Verify ee mixins implement every method OSS code calls via super().
# -------------------------------------------------------------------
EXPECTED_MIXIN_METHODS = {
    # Called unconditionally by team.py:1354 + project.py:690.
    "ee.api.rbac.access_control.AccessControlViewSetMixin": [
        "dangerously_get_required_scopes",
    ],
}
for path, methods in EXPECTED_MIXIN_METHODS.items():
    mod_name, _, cls_name = path.rpartition(".")
    try:
        cls = getattr(importlib.import_module(mod_name), cls_name)
        for m in methods:
            if not hasattr(cls, m):
                failures.append(f"MIXIN MISSING METHOD: {path}.{m}")
    except Exception as e:
        failures.append(f"MIXIN INSPECT FAIL {path}: {type(e).__name__}: {e}")

# -------------------------------------------------------------------
# 3) Smoke the ORM. Each query must build SQL without FieldError.
#    Empty result set is fine, the goal is "Django can resolve the field".
# -------------------------------------------------------------------
from ee.models import AccessControl, DashboardPrivilege, Role, RoleMembership  # noqa: E402
from ee.models.license import License  # noqa: E402

ORM_PROBES = [
    ("AccessControl team_id", lambda: AccessControl.objects.filter(team_id=1).exists()),
    (
        "AccessControl team__org traversal",
        lambda: AccessControl.objects.filter(
            team__organization_id="00000000-0000-0000-0000-000000000000"
        ).exists(),
    ),
    (
        "AccessControl org_member__user traversal",
        lambda: AccessControl.objects.filter(organization_member__user_id=1).exists(),
    ),
    (
        "Role.organization FK",
        lambda: Role.objects.filter(organization_id="00000000-0000-0000-0000-000000000000").exists(),
    ),
    ("RoleMembership user", lambda: RoleMembership.objects.filter(user_id=1).exists()),
    (
        "RoleMembership role__organization traversal",
        lambda: RoleMembership.objects.filter(
            role__organization_id="00000000-0000-0000-0000-000000000000"
        ).exists(),
    ),
    (
        "DashboardPrivilege user + dashboard_id + level",
        lambda: list(
            DashboardPrivilege.objects.filter(user_id=1).values_list("dashboard_id", "level")
        ),
    ),
    ("License first_valid", lambda: License.objects.first_valid()),
]
for label, probe in ORM_PROBES:
    try:
        probe()
    except Exception as e:
        failures.append(f"ORM PROBE FAIL {label}: {type(e).__name__}: {e}")

# -------------------------------------------------------------------
# 4) Confirm User.role_memberships reverse name resolves.
#    The exact bug that 500'd every authenticated request before fix 0046.
# -------------------------------------------------------------------
try:
    from posthog.models import User

    if not hasattr(User, "role_memberships"):
        failures.append(
            "REVERSE FK MISSING: User.role_memberships (RoleMembership.user.related_name "
            "must be 'role_memberships', not 'ee_role_memberships')"
        )
except Exception as e:
    failures.append(f"REVERSE FK CHECK FAIL: {type(e).__name__}: {e}")

# -------------------------------------------------------------------
# 5) Every existing org must have available_product_features populated.
#    If empty, multi-project / advanced perms / etc. silently fail.
# -------------------------------------------------------------------
try:
    from posthog.models.organization import Organization

    bad = []
    for org in Organization.objects.all():
        if not org.available_product_features:
            bad.append(f"  org {org.id} ({org.name}) has empty available_product_features")
    if bad:
        failures.append(
            "ORG FEATURE BACKFILL MISSING (run migration 0044 or call "
            "sync_all_organization_available_product_features task):\n" + "\n".join(bad)
        )
except Exception as e:
    failures.append(f"ORG FEATURE CHECK FAIL: {type(e).__name__}: {e}")

# -------------------------------------------------------------------
# 6) URL resolver: confirm routes exist (don't hit them, just resolve).
# -------------------------------------------------------------------
try:
    from django.urls import resolve

    URL_PROBES = [
        "/api/projects/1/groups_types/",
        "/api/projects/1/experiments/",
        "/api/projects/1/experiment_holdouts/",
        "/api/projects/1/experiment_saved_metrics/",
        "/api/environments/1/conversations/",
        "/api/environments/1/core_memory/",
        "/api/billing/",
        "/_preflight/",
    ]
    for url in URL_PROBES:
        try:
            resolve(url)
        except Exception as e:
            failures.append(f"URL PROBE FAIL {url}: {type(e).__name__}: {e}")
except Exception as e:
    failures.append(f"URL CHECK SETUP FAIL: {type(e).__name__}: {e}")

# -------------------------------------------------------------------
# Report
# -------------------------------------------------------------------
if failures:
    print("\n" + "=" * 70)
    print(f"FAILED: {len(failures)} smoke check(s)")
    print("=" * 70)
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
else:
    print(
        f"OK: all smoke checks passed ("
        f"{len(EE_IMPORTS)} imports, "
        f"{sum(len(v) for v in EXPECTED_MIXIN_METHODS.values())} mixin methods, "
        f"{len(ORM_PROBES)} ORM probes, 1 reverse-FK, 1 org-feature, "
        f"{len(URL_PROBES)} URL probes)"
    )
    sys.exit(0)
