"""FOSS stub for ee.models.explicit_team_membership.ExplicitTeamMembership.

Upstream: team-level membership overrides for RBAC. FOSS: empty.
"""

from django.db import models


class ExplicitTeamMembership(models.Model):
    class Meta:
        app_label = "ee"
        db_table = "ee_explicitteammembership"
