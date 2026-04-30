"""FOSS stub for ee.models.dashboard_privilege.DashboardPrivilege.

Upstream: per-user dashboard ACLs (EE feature). FOSS: empty table.
"""

from django.db import models


class DashboardPrivilege(models.Model):
    class Meta:
        app_label = "ee"
        db_table = "ee_dashboardprivilege"
