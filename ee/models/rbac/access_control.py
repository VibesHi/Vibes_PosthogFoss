"""FOSS stub for ee.models.rbac.access_control.AccessControl.

Returns no rows from any query so OSS callers see "no access control rules
configured" -- which means default-allow access. This matches the
EE_AVAILABLE=False behavior in posthog/.

Field set is the SUBSET of upstream's that's referenced by hot code paths
NOT gated behind `if EE_AVAILABLE:`. Notable OSS callers:
  - posthog.models.team.team.all_users_with_access() (signup flow)
  - posthog.rbac.user_access_control.UserAccessControl (ALWAYS exercised
    once `available_product_features` includes `advanced_permissions`,
    which is the case on FOSS post-License-stub change). Uses both
    `team__organization_id=...` (FK traversal, requires real FK) and
    `ac.team.organization_id` (descriptor access, requires real FK).
  - posthog.api.user.UserSerializer rendered on every page load.

Django validates filter() kwargs against model fields BEFORE running SQL,
so missing fields raise FieldError instead of returning empty querysets.
The `team` FK is declared with db_column="team_id" + db_constraint=False
so it reuses the existing INT4 column without forcing a type-promoting
ALTER TABLE against posthog_team's BIGINT id. Columns stay NULL forever
on FOSS because no UI inserts AccessControl rows. See migrations
0042_accesscontrol_organization_member_role.py and
0045_accesscontrol_team_fk.py.
"""

from django.db import models


class AccessControl(models.Model):
    # Real FK so `.filter(team=t)`, `.filter(team__organization_id=...)`,
    # and `ac.team` all work. db_column keeps the existing INT4 column;
    # db_constraint=False skips creating a real FK constraint at the DB
    # level (avoids INT4 vs posthog_team.id BIGINT type clash).
    team = models.ForeignKey(
        "posthog.Team",
        on_delete=models.CASCADE,
        related_name="ee_access_controls",
        null=True,
        db_column="team_id",
        db_constraint=False,
    )
    resource = models.CharField(max_length=200, null=True)
    resource_id = models.CharField(max_length=200, null=True)
    access_level = models.CharField(max_length=200, null=True)

    # Optional FK scoping. Both stay NULL on FOSS; queries with
    # `organization_member=None, role=None` return no rows since the table
    # itself stays empty. Required so signup's all_users_with_access() can
    # build its `.filter(...)` without FieldError.
    organization_member = models.ForeignKey(
        "posthog.OrganizationMembership",
        on_delete=models.CASCADE,
        related_name="ee_access_controls",
        null=True,
    )
    role = models.ForeignKey(
        "ee.Role",
        on_delete=models.CASCADE,
        related_name="ee_access_controls",
        null=True,
    )

    class Meta:
        app_label = "ee"
        db_table = "ee_accesscontrol"
