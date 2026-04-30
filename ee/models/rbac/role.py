"""FOSS stub for ee.models.rbac.role.

Upstream defines Role + RoleMembership as part of PostHog's RBAC. Self-host
without RBAC means no access checks; all org members get full access. These
stubs let migrations referencing `to="ee.role"` resolve and queries return
no rows.
"""

from django.db import models


class Role(models.Model):
    name = models.CharField(max_length=200)

    class Meta:
        app_label = "ee"
        db_table = "ee_role"

    def __str__(self) -> str:
        return f"<stub Role {self.id} {self.name!r}>"


class RoleMembership(models.Model):
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="role_memberships")

    class Meta:
        app_label = "ee"
        db_table = "ee_rolemembership"
