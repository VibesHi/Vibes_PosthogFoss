"""FOSS stub for ee.models.rbac.role.

Upstream defines Role + RoleMembership as part of PostHog's RBAC. Self-host
without RBAC means no access checks; all org members get full access. These
stubs let migrations referencing `to="ee.role"` resolve and queries return
no rows.

Field set mirrors the SUBSET of upstream's fields that's referenced from
non-EE code paths NOT gated behind `if EE_AVAILABLE:`. Notable callers:

  - posthog.rbac.user_access_control.UserAccessControl._user_role_ids:
    `self._user.role_memberships.select_related("role").all()` -- requires
    `RoleMembership.user.related_name == "role_memberships"` (NOT a fork-
    private name). Fires on every authenticated request now that the
    synthetic license unlocks ADVANCED_PERMISSIONS.
  - posthog.approvals.{policies,permissions,models,serializers}: filter
    `RoleMembership.objects.filter(role__organization=org)` and read
    `role.organization_id` directly -- requires `Role.organization` FK.
  - posthog.api.role_external_reference.OrganizationRoleScopedPrimaryKeyRelatedField:
    filters `Role.objects.filter(organization=org)`.

Django ORM validates kwargs against model fields BEFORE running SQL, so
missing fields raise FieldError on filter/traverse instead of returning
empty querysets. We declare the FKs but leave them all NULL on FOSS (no
UI inserts roles). See migrations 0043 and 0046.
"""

from django.db import models


class Role(models.Model):
    name = models.CharField(max_length=200)

    # Required by approvals/* and role_external_reference filtering.
    # `.filter(organization=org)`, `.filter(role__organization=org)`,
    # `role.organization_id` -- all dead-code-equivalent on FOSS (table
    # stays empty), but Django needs the field to resolve queries.
    organization = models.ForeignKey(
        "posthog.Organization",
        on_delete=models.CASCADE,
        related_name="ee_roles",
        null=True,
    )

    class Meta:
        app_label = "ee"
        db_table = "ee_role"

    def __str__(self) -> str:
        return f"<stub Role {self.id} {self.name!r}>"


class RoleMembership(models.Model):
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="role_memberships")

    # related_name MUST be "role_memberships" so `user.role_memberships`
    # resolves -- posthog.rbac.user_access_control reads it on every
    # authenticated request. Fork's private "ee_role_memberships" was
    # invisible to that reverse accessor and produced AttributeError
    # the moment ADVANCED_PERMISSIONS was unlocked.
    user = models.ForeignKey(
        "posthog.User",
        on_delete=models.CASCADE,
        related_name="role_memberships",
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
