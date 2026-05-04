"""Add `Role.organization` FK and rename `RoleMembership.user.related_name`.

Why these two together: both unblock OSS code paths that fire once
`available_product_features` includes RBAC features (now the FOSS
default thanks to the synthetic license).

1. Role.organization FK
   posthog.approvals.{policies,permissions,models,serializers} all do
   `RoleMembership.objects.filter(role__organization=org)` and
   `role.organization_id`. posthog.api.role_external_reference filters
   `Role.objects.filter(organization=org)` via OrgScopedPrimaryKeyRelatedField.
   No FK -> FieldError before SQL runs.

2. RoleMembership.user.related_name -> "role_memberships"
   posthog.rbac.user_access_control._user_role_ids reads
   `self._user.role_memberships.select_related("role").all()` on every
   authenticated request when ADVANCED_PERMISSIONS is enabled. Fork's
   prior private `ee_role_memberships` was invisible to that accessor.
   This is a state-only rename: same DB column, same constraint, only
   Django's reverse-accessor name changes. Zero DB ops.

Existing PostHog deployments importing this fork on top of real EE data:
the `ee_role.organization_id` column likely already exists. AddField on
a nullable FK with no default is a no-op when the column is already
present (Django's schema editor checks via introspection). The
`AlterField` for related_name is purely Python state.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ee", "0045_accesscontrol_team_fk"),
        ("posthog", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="role",
            name="organization",
            field=models.ForeignKey(
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name="ee_roles",
                to="posthog.organization",
            ),
        ),
        migrations.AlterField(
            model_name="rolemembership",
            name="user",
            field=models.ForeignKey(
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name="role_memberships",
                to="posthog.user",
            ),
        ),
    ]
