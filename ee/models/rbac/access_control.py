"""FOSS stub for ee.models.rbac.access_control.AccessControl.

Returns no rows from any query so OSS callers see "no access control rules
configured" -- which means default-allow access. This matches the
EE_AVAILABLE=False behavior in posthog/.

Field set is the SUBSET of upstream's that's referenced by hot code paths
NOT gated behind `if EE_AVAILABLE:` (notably posthog.models.team.team
:all_users_with_access(), called by signup -> create_team -> celery task).
We need the FKs to exist on the model AND in Postgres because Django
validates filter() kwargs against model fields BEFORE running SQL --
omitting them turns the query into FieldError. Columns stay null forever
on FOSS because no UI inserts AccessControl rows. See migration
0002_accesscontrol_add_organization_member_role.py.
"""

from django.db import models


class AccessControl(models.Model):
    team_id = models.IntegerField(null=True)
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
