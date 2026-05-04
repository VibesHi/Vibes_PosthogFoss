"""FOSS stub for ee.models.dashboard_privilege.DashboardPrivilege.

Upstream: per-user dashboard ACLs (EE feature). FOSS: empty table.

posthog.user_permissions.UserPermissions.dashboard_privileges (cached_property)
does:

    DashboardPrivilege.objects.filter(user=self.user).values_list(
        "dashboard_id", "level"
    )

This fires whenever someone opens a dashboard whose `restriction_level`
is `ONLY_COLLABORATORS_CAN_EDIT` AND the user isn't owner/admin. Without
the fields below, Django raises FieldError before SQL runs. Empty table,
NULL columns -- query returns [] and the call site falls back to
PrivilegeLevel.CAN_VIEW.

See migration 0047_dashboardprivilege_fields.py.
"""

from django.db import models


class DashboardPrivilege(models.Model):
    user = models.ForeignKey(
        "posthog.User",
        on_delete=models.CASCADE,
        related_name="ee_dashboard_privileges",
        null=True,
    )
    dashboard = models.ForeignKey(
        "dashboards.Dashboard",
        on_delete=models.CASCADE,
        related_name="ee_privileges",
        null=True,
    )
    level = models.PositiveSmallIntegerField(null=True)

    class Meta:
        app_label = "ee"
        db_table = "ee_dashboardprivilege"
