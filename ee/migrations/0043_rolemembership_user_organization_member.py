"""Add `user` and `organization_member` FK columns to `ee_rolemembership`.

Mirror of the AccessControl 0042 migration. The RoleMembership stub had
neither column, so any non-EE code path that does:

    RoleMembership.objects.filter(user=..., organization_member__in=[...])

(see posthog.models.user.User.teams, posthog.approvals.*) would raise
FieldError BEFORE hitting the DB. All call sites are gated behind feature
flags or policy configs that stay False on a vanilla FOSS deploy, but the
gate flips on a Django-admin tweak and we don't want one toggle to turn
into a 500 storm. Both columns stay NULL on FOSS (no UI inserts roles).

Idempotent: nullable column add. Lock window is microseconds (table empty).
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ee", "0042_accesscontrol_organization_member_role"),
    ]

    operations = [
        migrations.AddField(
            model_name="rolemembership",
            name="user",
            field=models.ForeignKey(
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name="ee_role_memberships",
                to="posthog.user",
            ),
        ),
        migrations.AddField(
            model_name="rolemembership",
            name="organization_member",
            field=models.ForeignKey(
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name="ee_role_memberships",
                to="posthog.organizationmembership",
            ),
        ),
    ]
