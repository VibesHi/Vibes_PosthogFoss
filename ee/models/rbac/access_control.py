"""FOSS stub for ee.models.rbac.access_control.AccessControl.

Returns no rows from any query so OSS callers see "no access control rules
configured" -- which means default-allow access. This matches the
EE_AVAILABLE=False behavior in posthog/.
"""

from django.db import models


class AccessControl(models.Model):
    team_id = models.IntegerField(null=True)
    resource = models.CharField(max_length=200, null=True)
    resource_id = models.CharField(max_length=200, null=True)
    access_level = models.CharField(max_length=200, null=True)

    class Meta:
        app_label = "ee"
        db_table = "ee_accesscontrol"
