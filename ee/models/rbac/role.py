"""FOSS stub for ee.models.rbac.role.

Upstream defines Role + RoleMembership as part of PostHog's RBAC. Self-host
without RBAC means no access checks; all org members get full access. These
stubs let migrations referencing `to="ee.role"` resolve and queries return
no rows.

Field set on RoleMembership intentionally mirrors the SUBSET of upstream's
that's referenced from non-EE code paths NOT gated behind `if EE_AVAILABLE:`
(notably posthog.models.user.User.teams via AvailableFeature.ADVANCED_PERMISSIONS,
posthog.approvals.policies._has_bypass / _actor_is_approver, and
posthog.approvals.permissions.CanApprove). Each call site IS gated by a
runtime check (feature flag, policy config), so on a vanilla FOSS deploy
these queries never fire. But the gate is one Django-admin click away from
flipping, and Django ORM validates `.filter()` kwargs against model fields
BEFORE running SQL, so missing columns turn into FieldError 500s. Cheap
insurance to declare them now. Columns stay NULL forever on FOSS.
See migration 0043_rolemembership_user_organization_member.py.
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

    user = models.ForeignKey(
        "posthog.User",
        on_delete=models.CASCADE,
        related_name="ee_role_memberships",
        null=True,
    )
    organization_member = models.ForeignKey(
        "posthog.OrganizationMembership",
        on_delete=models.CASCADE,
        related_name="ee_role_memberships",
        null=True,
    )

    class Meta:
        app_label = "ee"
        db_table = "ee_rolemembership"
