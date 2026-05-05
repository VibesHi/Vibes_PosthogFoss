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
# 6) Materialized columns FOSS patch regression check.
#    posthog/clickhouse/materialized_columns.py has a FOSS `else:` branch
#    (see README.prod.md "FOSS source patches") that wires the read path
#    to ee/clickhouse/materialized_columns/columns.py. If either side
#    regresses to upstream-pristine the HogQL printer silently emits
#    JSONExtractRaw on every property access -- 5-10x slowdown on
#    Web/Product Analytics queries with no error. We can't catch this with
#    Python type checks alone, so probe the wiring directly.
# -------------------------------------------------------------------
try:
    from posthog.clickhouse.materialized_columns import (
        get_enabled_materialized_columns,
        get_materialized_column_for_property,
    )
    from ee.clickhouse.materialized_columns.columns import materialize as _ee_materialize  # noqa: F401

    # The EE module must expose the real `materialize` callable, not a stub.
    # If it's a no-op stub, calling materialize("events", "$probe") would
    # silently do nothing (the original FOSS strip behavior).
    if not callable(_ee_materialize) or _ee_materialize.__module__ != "ee.clickhouse.materialized_columns.columns":
        failures.append(
            "MATERIALIZED COLUMNS REGRESSION: ee.clickhouse.materialized_columns.columns.materialize "
            "is not a real callable (looks like the FOSS stub got re-introduced)"
        )

    # The introspection must return a dict (possibly empty), not None or {}.
    # Empty is fine -- means no columns materialized yet -- but the function
    # must execute the system.columns query without raising.
    cols = get_enabled_materialized_columns("events")
    if not isinstance(cols, dict):
        failures.append(
            f"MATERIALIZED COLUMNS REGRESSION: get_enabled_materialized_columns('events') "
            f"returned {type(cols).__name__}, expected dict"
        )

    # The function must NOT unconditionally return None (the FOSS pre-Tier-1
    # behavior). On a fresh CH install events table has $session_id,
    # $window_id, $group_0..4 baked in via EVENTS_TABLE_SQL -- if any of
    # those are present, get_materialized_column_for_property must find them.
    if cols:
        # Pick any (property, table_column) pair from the introspection
        # and verify get_materialized_column_for_property returns the same.
        (sample_prop, sample_col), sample_mc = next(iter(cols.items()))
        result = get_materialized_column_for_property("events", sample_col, sample_prop)
        if result is None:
            failures.append(
                f"MATERIALIZED COLUMNS REGRESSION: get_materialized_column_for_property "
                f"returned None for ({sample_prop!r}, {sample_col!r}) which IS in "
                f"get_enabled_materialized_columns -- FOSS `else:` branch likely missing or broken"
            )
except Exception as e:
    failures.append(f"MATERIALIZED COLUMNS PROBE FAIL: {type(e).__name__}: {e}")

# -------------------------------------------------------------------
# 7) URL resolver: confirm routes exist (don't hit them, just resolve).
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
        f"1 materialized-columns probe, "
        f"{len(URL_PROBES)} URL probes)"
    )
    sys.exit(0)
